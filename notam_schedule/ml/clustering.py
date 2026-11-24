"""K-means over an LSA representation (TF-IDF -> SVD) for pattern discovery and label propagation.

How K-means is used in this project:
  1. Exploration: groups NOTAMs with similar wording and shows each group's characteristic terms.
  2. Cluster-then-label: in "pure" clusters (almost all labeled members share the same class) the
     label is propagated to the unlabeled members (weak label, source = "cluster"), with two guards:
     NOT_RUNWAY is never propagated to a text that mentions a runway (a missed restriction is the
     costly error), and a member must lie as close to the centroid as the labeled members do.
  3. Active learning: clusters with many unlabeled or mixed labels feed the review queue.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np
from sklearn.cluster import KMeans
from sklearn.decomposition import TruncatedSVD
from sklearn.metrics import silhouette_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import Normalizer

from ..detection.rules import mentions_runway
from . import NOT_RUNWAY
from .features import build_vectorizer
from .labeling import LabelSet


@dataclass
class Cluster:
    id: int
    size: int
    terms: list[str]
    label_counts: dict[str, int]      # distribution of known labels
    unlabeled: int
    purity: float | None              # share of the majority class among labeled members
    majority: str | None
    examples: list[str]
    action: str


@dataclass
class ClusterResult:
    k: int
    silhouette: float
    assignment: np.ndarray            # cluster of each NOTAM
    clusters: list[Cluster]
    distance: np.ndarray              # distance of each NOTAM to its cluster centroid (LSA space)


def _embed(texts: list[str], seed: int):
    vec = build_vectorizer().transformer_list[0][1]  # words only: readable terms per cluster
    X = vec.fit_transform(texts)
    n_comp = max(2, min(100, X.shape[1] - 1, X.shape[0] - 1))
    lsa = make_pipeline(TruncatedSVD(n_components=n_comp, random_state=seed), Normalizer(copy=False))
    return vec, X, lsa.fit_transform(X)


def choose_k(Z: np.ndarray, ks: range, seed: int) -> tuple[int, float]:
    best = (ks.start, -1.0)
    sample = min(len(Z), 3000)
    for k in ks:
        if k >= len(Z):
            break
        lab = KMeans(n_clusters=k, n_init=5, random_state=seed).fit_predict(Z)
        if len(set(lab)) < 2:
            continue
        s = silhouette_score(Z, lab, sample_size=sample, random_state=seed)
        if s > best[1]:
            best = (k, float(s))
    return best


def cluster_corpus(ls: LabelSet, k: int | None = None, seed: int = 42, min_purity: float = 0.95,
                   min_labeled: int = 5) -> ClusterResult:
    if len(ls.texts) < 10:
        raise ValueError("Corpus pequeno demais para agrupar (mínimo 10 NOTAMs).")
    vec, X, Z = _embed(ls.texts, seed)
    if k is None:
        k, _ = choose_k(Z, range(4, max(5, min(40, len(Z) // 8)) + 1), seed)
    km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(Z)
    lab = km.labels_
    sil = float(silhouette_score(Z, lab, sample_size=min(len(Z), 3000), random_state=seed)) if k > 1 else 0.0
    vocab = np.array(vec.get_feature_names_out())
    distance = np.linalg.norm(Z - km.cluster_centers_[lab], axis=1)

    clusters = []
    for c in range(k):
        idx = np.where(lab == c)[0]
        known = Counter(ls.labels[i] for i in idx if ls.labels[i])
        n_lab = sum(known.values())
        majority, purity = None, None
        if n_lab:
            majority, cnt = known.most_common(1)[0]
            purity = round(cnt / n_lab, 3)
        mean = np.asarray(X[idx].mean(axis=0)).ravel()
        terms = [t for t in vocab[mean.argsort()[::-1][:12]] if not t.startswith("_")][:8]
        # examples closest to the centroid
        examples = [ls.texts[i][:160] for i in idx[distance[idx].argsort()[:3]]]
        unlabeled = len(idx) - n_lab
        if n_lab >= min_labeled and purity is not None and purity >= min_purity:
            blocked = majority == NOT_RUNWAY and any(
                ls.labels[i] is None and mentions_runway(ls.texts[i]) for i in idx)
            if blocked:  # the propagation guard keeps these out; a human must decide (feeds the review queue)
                action = f"rotular sem rótulo que citam pista (puro {majority})"
            else:
                action = f"puro ({majority}) — propagar rótulo" if unlabeled else f"puro ({majority})"
        elif n_lab == 0:
            action = "sem rótulo — rotular amostra"
        elif purity is not None and purity < 0.8:
            action = "misturado — revisar"
        else:
            action = "rotular mais exemplos"
        clusters.append(Cluster(c, len(idx), terms, dict(known), unlabeled, purity, majority, examples, action))
    return ClusterResult(k, sil, lab, clusters, distance)


def propagate_labels(ls: LabelSet, cr: ClusterResult, min_purity: float = 0.95, min_labeled: int = 5,
                     radius_quantile: float = 0.9) -> int:
    """Cluster-then-label: apply the majority label of pure clusters to unlabeled members. Returns count.

    Guards (see module docstring): no NOT_RUNWAY for texts that mention a runway, and the member's
    distance to the centroid must not exceed the `radius_quantile` of the labeled members' distances.
    """
    n = 0
    for cl in cr.clusters:
        if not (cl.majority and cl.purity is not None and cl.purity >= min_purity
                and sum(cl.label_counts.values()) >= min_labeled):
            continue
        idx = np.where(cr.assignment == cl.id)[0]
        labeled = [i for i in idx if ls.labels[i] is not None]
        radius = float(np.quantile(cr.distance[labeled], radius_quantile))
        for i in idx:
            if ls.labels[i] is not None or cr.distance[i] > radius:
                continue
            if cl.majority == NOT_RUNWAY and mentions_runway(ls.texts[i]):
                continue
            ls.labels[i], ls.sources[i] = cl.majority, "cluster"
            n += 1
    return n
