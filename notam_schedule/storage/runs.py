"""Analysis runs and their impacts in SQLite, so each run can be compared with the previous one."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import astuple, fields
from pathlib import Path

from ..config import RUNS_DB
from ..domain.changes import ImpactRecord

RECORD_FIELDS = [f.name for f in fields(ImpactRecord)]
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    schedule_file TEXT, schedule_sha256 TEXT,
    period_start TEXT, period_end TEXT,
    detector TEXT, params TEXT,
    flights INTEGER, impacts INTEGER,
    errors TEXT, out_of_coverage TEXT
);
CREATE TABLE IF NOT EXISTS impact (
    run_id INTEGER NOT NULL REFERENCES run(id),
    {", ".join(f"{name} TEXT" for name in RECORD_FIELDS)}
);
CREATE INDEX IF NOT EXISTS impact_run ON impact(run_id);
"""


class RunStore:
    def __init__(self, path: str | Path = RUNS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def record(self, meta: dict, records: list[ImpactRecord]) -> int:
        cur = self.db.execute(
            "INSERT INTO run (started_at, schedule_file, schedule_sha256, period_start, period_end, detector, params, "
            "flights, impacts, errors, out_of_coverage) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (meta["started_at"], meta.get("schedule_file"), meta.get("schedule_sha256"), meta.get("period_start"),
             meta.get("period_end"), meta.get("detector"), json.dumps(meta.get("params", {}), ensure_ascii=False),
             meta.get("flights"), len(records), json.dumps(meta.get("errors", {}), ensure_ascii=False),
             json.dumps(meta.get("out_of_coverage", []), ensure_ascii=False)))
        run_id = cur.lastrowid
        self.db.executemany(f"INSERT INTO impact VALUES (?{',?' * len(RECORD_FIELDS)})",
                            [(run_id, *astuple(r)) for r in records])
        self.db.commit()
        return run_id

    def latest(self) -> tuple[dict, list[ImpactRecord]] | None:
        """Most recent run and its impacts, or None when nothing was recorded yet."""
        row = self.db.execute("SELECT * FROM run ORDER BY id DESC LIMIT 1").fetchone()
        if row is None:
            return None
        records = [ImpactRecord(*(r[name] for name in RECORD_FIELDS))
                   for r in self.db.execute("SELECT * FROM impact WHERE run_id = ?", (row["id"],))]
        return dict(row), records
