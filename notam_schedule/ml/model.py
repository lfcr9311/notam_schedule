"""Semi-supervised classifier (self-training) and safety-oriented evaluation.

- Model: TF-IDF (words + chars) -> SelfTrainingClassifier(balanced LogisticRegression).
  Unlabeled NOTAMs are part of training: the model iteratively labels high-confidence ones.
- Evaluation: preferably on human labels (gold), via leakage-free cross-validation.
  Compares model, rules and hybrid (rule OR model). The decision threshold is chosen to reach
  the target recall (e.g. 98%) for runway-NOTAM detection — missing a critical NOTAM costs more
  than reviewing a false positive.
"""
from __future__ import annotations

import hashlib
import json
import logging
import warnings
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report, confusion_matrix
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.semi_supervised import SelfTrainingClassifier

from .. import __version__
from ..config import MODEL_FILE, MODELS_DIR
from . import NOT_RUNWAY
from .features import build_vectorizer
from .labeling import LABELS, LabelSet, rule_label

log = logging.getLogger(__name__)
IDX = {label: i for i, label in enumerate(LABELS)}


def make_pipeline(seed: int = 42, self_training_threshold: float = 0.9) -> Pipeline:
    base = LogisticRegression(max_iter=3000, class_weight="balanced", C=4.0, random_state=seed)
    return Pipeline([
        ("vec", build_vectorizer()),
        ("clf", SelfTrainingClassifier(base, threshold=self_training_threshold, max_iter=10)),
    ])


def _encode(labels: list[str | None]) -> np.ndarray:
    return np.array([IDX[label] if label else -1 for label in labels])


def _runway_prob_and_category(pipe: Pipeline, texts: list[str]) -> tuple[np.ndarray, list[str], np.ndarray]:
    proba = pipe.predict_proba(texts)
    classes = [LABELS[c] for c in pipe.named_steps["clf"].classes_]
    neg = classes.index(NOT_RUNWAY) if NOT_RUNWAY in classes else None
    p_runway = 1 - proba[:, neg] if neg is not None else np.ones(len(texts))
    cat_proba = proba.copy()
    if neg is not None:
        cat_proba[:, neg] = -1
    top = cat_proba.argmax(axis=1)
    return p_runway, [classes[i] for i in top], cat_proba[np.arange(len(texts)), top]


@dataclass
class Evaluation:
    basis: str                                  # gold set / human review labels / weak labels
    n: int
    threshold: float
    target_recall: float
    model: dict = field(default_factory=dict)   # binary metrics runway x not-runway
    rules: dict = field(default_factory=dict)
    hybrid: dict = field(default_factory=dict)
    per_category: dict = field(default_factory=dict)
    confusion_matrix: dict = field(default_factory=dict)


def _binary_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict:
    tp = int(((y_true == 1) & (y_pred == 1)).sum())
    fp = int(((y_true == 0) & (y_pred == 1)).sum())
    fn = int(((y_true == 1) & (y_pred == 0)).sum())
    tn = int(((y_true == 0) & (y_pred == 0)).sum())
    rec = tp / (tp + fn) if tp + fn else None
    prec = tp / (tp + fp) if tp + fp else None
    return {"recall": None if rec is None else round(rec, 4), "precision": None if prec is None else round(prec, 4),
            "tp": tp, "fp": fp, "fn": fn, "tn": tn}


def choose_threshold(y_true: np.ndarray, p_runway: np.ndarray, rule_pos: np.ndarray, target_recall: float) -> float:
    """Highest threshold whose hybrid (rule OR p>=threshold) reaches the target recall."""
    pos = y_true == 1
    if not pos.any():
        return 0.5
    for t in np.round(np.arange(0.95, 0.04, -0.05), 2):
        pred = rule_pos | (p_runway >= t)
        if (pred & pos).sum() / pos.sum() >= target_recall:
            return float(t)
    return 0.05


def evaluate(ls: LabelSet, target_recall: float = 0.98, seed: int = 42, folds: int = 5) -> Evaluation | None:
    y = _encode(ls.labels)
    human = np.array([s == "human" for s in ls.sources])
    gold = np.array(ls.gold, dtype=bool) if len(ls.gold) == len(y) else np.zeros(len(y), dtype=bool)

    def enough(mask: np.ndarray) -> bool:
        return mask.sum() >= 20 and len(set(y[mask])) >= 2

    # Preference: random gold sample (unbiased) > human labels from the review queue (biased toward hard
    # cases) > weak labels (optimistic: the model learns from the rules themselves).
    if enough(gold & (y >= 0)):
        target, basis = gold & (y >= 0), "gold set (random sample, human labels)"
    elif enough(human & (y >= 0)):
        target, basis = human & (y >= 0), "human labels from the review queue — BIASED toward hard cases; label a gold set"
    else:
        target = (y >= 0) & ~human
        basis = "weak labels — OPTIMISTIC (the model learns from the rules themselves); label a gold set"
    idx = np.where(target)[0]
    if len(idx) < 10 or len(set(y[idx])) < 2:
        return None
    counts = np.bincount(y[idx])
    k = max(2, int(min(folds, counts[counts > 0].min())))
    p_runway = np.zeros(len(ls.texts))
    cat_pred: list[str] = [""] * len(ls.texts)
    skf = StratifiedKFold(n_splits=k, shuffle=True, random_state=seed)
    texts = np.array(ls.texts, dtype=object)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        splits = list(skf.split(idx, y[idx]))
    for _, te in splits:
        test_idx = idx[te]
        train_mask = np.ones(len(y), dtype=bool)
        train_mask[test_idx] = False          # removed from training (not even as unlabeled)
        y_tr = y[train_mask]
        if len(set(y_tr[y_tr >= 0])) < 2:
            continue
        pipe = make_pipeline(seed).fit(list(texts[train_mask]), y_tr)
        pr, cats, _ = _runway_prob_and_category(pipe, list(texts[test_idx]))
        p_runway[test_idx] = pr
        for j, i in enumerate(test_idx):
            cat_pred[i] = cats[j]

    y_true_lab = [LABELS[y[i]] for i in idx]
    y_bin = np.array([0 if label == NOT_RUNWAY else 1 for label in y_true_lab])
    rule_labels = [rule_label(ls.notams[i]) if ls.notams else None for i in idx]
    rule_pos = np.array([1 if (r and r != NOT_RUNWAY) else 0 for r in rule_labels])
    threshold = choose_threshold(y_bin, p_runway[idx], rule_pos.astype(bool), target_recall)
    model_pos = (p_runway[idx] >= threshold).astype(int)
    hybrid = (rule_pos.astype(bool) | model_pos.astype(bool)).astype(int)

    # hybrid final category: rule when available, else model
    pred_lab = []
    for j, i in enumerate(idx):
        if rule_labels[j] and rule_labels[j] != NOT_RUNWAY:
            pred_lab.append(rule_labels[j])
        elif model_pos[j]:
            pred_lab.append(cat_pred[i] or NOT_RUNWAY)
        else:
            pred_lab.append(NOT_RUNWAY)
    present = [label for label in LABELS if label in set(y_true_lab) | set(pred_lab)]
    rep = classification_report(y_true_lab, pred_lab, labels=present, output_dict=True, zero_division=0)
    cm = confusion_matrix(y_true_lab, pred_lab, labels=present)
    return Evaluation(
        basis=basis, n=len(idx), threshold=threshold, target_recall=target_recall,
        model=_binary_metrics(y_bin, model_pos), rules=_binary_metrics(y_bin, rule_pos),
        hybrid=_binary_metrics(y_bin, hybrid),
        per_category={k_: {m: round(v, 3) for m, v in d.items()} for k_, d in rep.items() if isinstance(d, dict)},
        confusion_matrix={"labels": present, "matrix": cm.tolist()},
    )


def train(ls: LabelSet, output: str | Path = MODELS_DIR, target_recall: float = 0.98, seed: int = 42,
          extra_card: dict | None = None) -> tuple[Path, dict]:
    y = _encode(ls.labels)
    if len(set(y[y >= 0])) < 2:
        raise ValueError("São necessárias ao menos 2 classes rotuladas para treinar.")
    ev = evaluate(ls, target_recall, seed)
    pipe = make_pipeline(seed).fit(ls.texts, y)
    clf = pipe.named_steps["clf"]
    auto = int((clf.labeled_iter_ > 0).sum())

    version = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    data_hash = hashlib.sha256("\n".join(f"{u}\t{label}" for u, label in zip(ls.uids, ls.labels)).encode()).hexdigest()[:16]
    card = {
        "version": version,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "package": __version__,
        "sklearn": sklearn.__version__,
        "algorithm": "TF-IDF (words 1-2g + chars 3-5g) + SelfTraining(balanced LogisticRegression)",
        "seed": seed,
        "data_hash": data_hash,
        "n_notams": len(ls.texts),
        "labels_by_source": ls.summary(),
        "labeled_by_self_training": auto,
        "runway_threshold": ev.threshold if ev else 0.5,
        "target_recall": target_recall,
        "evaluation": asdict(ev) if ev else None,
        **(extra_card or {}),
    }
    path = out / f"notam_clf_{version}.joblib"
    payload = {"pipeline": pipe, "labels": LABELS, "card": card}
    joblib.dump(payload, path)
    (out / f"notam_clf_{version}.json").write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    current = out / "current.joblib"
    current.unlink(missing_ok=True)
    try:
        current.symlink_to(path.name)
    except OSError:
        joblib.dump(payload, current)
    return path, card


class NotamModel:
    def __init__(self, path: str | Path = MODEL_FILE) -> None:
        obj = joblib.load(path)
        self.pipe: Pipeline = obj["pipeline"]
        self.card: dict = obj["card"]
        self.threshold: float = float(self.card.get("runway_threshold", 0.5))
        self.version: str = self.card.get("version", "?")

    def predict(self, texts: list[str]) -> list[tuple[float, str, float]]:
        """[(probability of being a runway NOTAM, most likely category, category probability)]"""
        if not texts:
            return []
        p_runway, cats, pc = _runway_prob_and_category(self.pipe, texts)
        return [(float(a), b, float(c)) for a, b, c in zip(p_runway, cats, pc)]
