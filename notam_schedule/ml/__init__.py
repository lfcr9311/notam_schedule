"""Semi-supervised detection of runway-condition NOTAMs.

Flow: corpus (SQLite) -> weak labels (rules + Q-code + human + K-means propagation)
-> self-training classifier (TF-IDF + logistic regression) -> review queue (active learning).
"""
from ..domain.categories import NOT_RUNWAY

__all__ = ["NOT_RUNWAY"]
