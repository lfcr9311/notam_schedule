"""Use case: train the semi-supervised detector (optionally with K-means label propagation first)."""
from __future__ import annotations

from pathlib import Path

from ..config import MODELS_DIR
from ..ml.clustering import cluster_corpus, propagate_labels
from ..ml.labeling import LabelSet
from ..ml.model import train


def train_detector(ls: LabelSet, output: str | Path = MODELS_DIR, target_recall: float = 0.98,
                   propagate: bool = True) -> tuple[Path, dict]:
    """Returns (model path, model card). With `propagate`, the card records the K-means step."""
    extra: dict = {}
    if propagate:
        cr = cluster_corpus(ls)
        n = propagate_labels(ls, cr)
        extra = {"kmeans": {"k": cr.k, "silhouette": round(cr.silhouette, 3), "propagated_labels": n}}
    return train(ls, output, target_recall, extra_card=extra)
