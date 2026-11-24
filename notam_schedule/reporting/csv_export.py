"""Flat CSV with one row per (flight, NOTAM) impact."""
from __future__ import annotations

import csv
from pathlib import Path

from ..domain.impact import AnalysisResult
from .formatting import impact_rows


def write_csv(res: AnalysisResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    head, rows = impact_rows(res)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(head)
        w.writerows(rows)
    return path
