"""Use case: record an analysis run and compare it with the previous one ("what changed")."""
from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from ..domain.changes import ChangeSet, ImpactRecord, diff_impacts
from ..domain.impact import AnalysisResult
from ..storage.runs import RunStore


def _sha256(path: str | Path) -> str | None:
    p = Path(path)
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None


def record_run(store: RunStore, res: AnalysisResult, schedule_file: str | Path, detector: str,
               params: dict, name: str | None = None) -> ChangeSet:
    """Compare this run with the latest recorded one, then record it. Returns the changes."""
    records = [ImpactRecord.from_impact(i) for i in res.impacts]
    dates = [f.date for f in res.flights]
    period = (min(dates), max(dates))
    previous = store.latest()
    changes = diff_impacts(previous[1] if previous else [], records, period,
                           previous[0] if previous else None)
    if previous is None:
        changes.changes = []  # first run: nothing to compare with, do not list every impact as new
    changes.run_id = store.record({
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "schedule_file": name or str(schedule_file), "schedule_sha256": _sha256(schedule_file),
        "period_start": period[0].isoformat(), "period_end": period[1].isoformat(),
        "detector": detector, "params": params, "flights": len(res.flights),
        "errors": res.errors, "out_of_coverage": res.out_of_coverage,
    }, records)
    return changes
