"""Storage layer: NOTAM corpus in SQLite (continuous collection) and human labels."""
from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import CORPUS_DB
from ..domain.notam import Notam, parse_dtg

SCHEMA = """
CREATE TABLE IF NOT EXISTS notams (
    uid TEXT PRIMARY KEY,           -- loc|number
    loc TEXT, number TEXT, ntype TEXT, b TEXT, c TEXT, d TEXT, e TEXT NOT NULL,
    raw TEXT, source TEXT, collected_at TEXT
);
-- Append-only: a new label never overwrites an old one; the latest row per uid is the current label.
CREATE TABLE IF NOT EXISTS labels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    uid TEXT NOT NULL REFERENCES notams(uid),
    label TEXT NOT NULL,
    author TEXT, created_at TEXT, note TEXT,
    label_set TEXT NOT NULL DEFAULT 'review'  -- gold (random sample, for evaluation) | review | simulated
);
CREATE INDEX IF NOT EXISTS labels_uid ON labels(uid, id);
"""
LABEL_SETS = ("gold", "review", "simulated")
LATEST = "SELECT l.* FROM labels l JOIN (SELECT uid, MAX(id) AS id FROM labels GROUP BY uid) m ON l.id = m.id"


def uid_of(n: Notam) -> str:
    return f"{n.loc}|{n.number}"


class Corpus:
    def __init__(self, path: str | Path = CORPUS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.row_factory = sqlite3.Row
        self._migrate_labels()
        self.db.executescript(SCHEMA)

    def _migrate_labels(self) -> None:
        """Old databases kept one label per NOTAM (uid PRIMARY KEY, overwritten). Convert to the append-only table."""
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(labels)")}
        if not cols or "id" in cols:
            return
        self.db.executescript("ALTER TABLE labels RENAME TO labels_v1;" + SCHEMA + """
            INSERT INTO labels (uid, label, author, created_at, note, label_set)
            SELECT uid, label, author, created_at, note,
                   CASE WHEN author = 'simulated' THEN 'simulated' ELSE 'review' END FROM labels_v1;
            DROP TABLE labels_v1;""")
        self.db.commit()

    def add(self, notams: list[Notam], source: str) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        before = self.count()
        self.db.executemany(
            "INSERT OR IGNORE INTO notams VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [(uid_of(n), n.loc, n.number, n.ntype, n.raw.get("b", ""), n.raw.get("c", ""), n.d, n.e,
              json.dumps(n.raw, ensure_ascii=False), source, now) for n in notams if n.e],
        )
        self.db.commit()
        return self.count() - before

    def count(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM notams").fetchone()[0]

    def all(self) -> list[tuple[str, Notam]]:
        out = []
        for r in self.db.execute("SELECT * FROM notams ORDER BY uid"):
            raw = json.loads(r["raw"] or "{}")
            b, _, _ = parse_dtg(r["b"])
            c, perm, est = parse_dtg(r["c"])
            out.append((r["uid"], Notam(r["number"], r["loc"], r["ntype"], raw.get("ref", ""), b, c, perm, est,
                                        r["d"], r["e"], raw)))
        return out

    # ------------------------------------------------------------ human labels
    def human_labels(self) -> dict[str, str]:
        """Current (latest) label of each NOTAM, from any label set."""
        return {r["uid"]: r["label"] for r in self.db.execute(LATEST)}

    def gold_uids(self) -> set[str]:
        """NOTAMs whose current label came from the random gold sample (unbiased evaluation set)."""
        return {r["uid"] for r in self.db.execute(LATEST) if r["label_set"] == "gold"}

    def label_history(self, uid: str) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM labels WHERE uid = ? ORDER BY id", (uid,))]

    def set_label(self, uid: str, label: str, author: str = "", note: str = "", label_set: str = "review") -> None:
        if label_set not in LABEL_SETS:
            raise ValueError(f"label_set must be one of {LABEL_SETS}")
        self.db.execute("INSERT INTO labels (uid, label, author, created_at, note, label_set) VALUES (?,?,?,?,?,?)",
                        (uid, label, author, datetime.now().isoformat(timespec="seconds"), note, label_set))

    def unlabeled_sample(self, n: int, seed: int = 42) -> list[tuple[str, Notam]]:
        """Uniform random sample of NOTAMs without a gold label (to build the gold set)."""
        import random
        gold = self.gold_uids()
        pool = [item for item in self.all() if item[0] not in gold]
        return random.Random(seed).sample(pool, min(n, len(pool)))

    def import_labels_csv(self, path: str | Path, valid: set[str], author: str = "") -> tuple[int, list[str]]:
        """Read a filled-in review queue or gold sample (columns uid, human_label and optional label_set).

        Lines starting with # are ignored. Rows without label_set count as 'review'.
        """
        ok, errors = 0, []
        known = {r[0] for r in self.db.execute("SELECT uid FROM notams")}
        with open(path, encoding="utf-8-sig", newline="") as f:
            lines = [ln for ln in f if not ln.startswith("#")]
        sample = "".join(lines[:5])
        reader = csv.DictReader(lines, delimiter=";" if sample.count(";") >= sample.count(",") else ",")
        for i, row in enumerate(reader, start=2):
            label = (row.get("human_label") or "").strip().upper()
            if not label:
                continue
            uid = (row.get("uid") or "").strip()
            label_set = (row.get("label_set") or "review").strip().lower()
            if label_set not in LABEL_SETS:
                errors.append(f"linha {i}: conjunto inválido '{label_set}'")
            elif label not in valid:
                errors.append(f"linha {i}: rótulo inválido '{label}'")
            elif uid not in known:
                errors.append(f"linha {i}: NOTAM '{uid}' não está no corpus")
            else:
                self.set_label(uid, label, author or row.get("author", ""), row.get("note", ""), label_set)
                ok += 1
        self.db.commit()
        return ok, errors
