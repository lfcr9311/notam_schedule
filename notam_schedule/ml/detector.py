"""ML detectors for the schedule analysis: hybrid (rule OR model) or model only. Implements domain.ports.Detector."""
from __future__ import annotations

from pathlib import Path

from ..config import MODEL_FILE
from ..detection.rules import classify, extract_details
from ..domain.categories import Classification
from ..domain.notam import Notam
from ..domain.text import normalize
from . import NOT_RUNWAY
from .model import NotamModel


class HybridDetector:
    """'hybrid' mode (recommended): relevant if the rule fires OR the model exceeds the threshold.
    'ml' mode: only the model decides (rules are still used to extract runway/distances)."""

    def __init__(self, model_path: str | Path = MODEL_FILE, mode: str = "hybrid",
                 threshold: float | None = None) -> None:
        if mode not in ("hybrid", "ml"):
            raise ValueError("mode must be 'hybrid' or 'ml'")
        self.model = NotamModel(model_path)
        self.mode = mode
        self.threshold = self.model.threshold if threshold is None else threshold
        self._cache: dict[str, tuple[float, str, float]] = {}

    def prepare(self, notams: list[Notam]) -> None:
        """Batch prediction (faster than one by one)."""
        new = [n.e for n in notams if n.e not in self._cache]
        for text, pred in zip(new, self.model.predict(new)):
            self._cache[text] = pred

    def __call__(self, notam: Notam) -> Classification:
        if notam.e not in self._cache:
            self.prepare([notam])
        p_runway, cat, _ = self._cache[notam.e]
        rule = classify(notam)
        ml_pos = p_runway >= self.threshold and cat != NOT_RUNWAY

        if self.mode == "hybrid" and rule.relevant:
            rule.source = "rules+ml" if ml_pos else "rules"
            rule.prob = round(p_runway, 3)
            return rule
        if ml_pos:
            # in 'ml' mode with a positive rule too, the rule category is more specific
            c = Classification(categories=[rule.primary if rule.relevant else cat],
                               source="rules+ml" if rule.relevant else "ml", prob=round(p_runway, 3))
            return extract_details(normalize(notam.e), c)
        return Classification(source="ml" if self.mode == "ml" else "rules", prob=round(p_runway, 3))
