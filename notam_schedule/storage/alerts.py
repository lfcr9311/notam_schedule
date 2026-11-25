"""NOTAM digests sent to the analysts (twice a day): what each one contained, for the RSS feed and the diff."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from ..config import ALERTS_DB

SCHEMA = """
CREATE TABLE IF NOT EXISTS digest (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot TEXT NOT NULL UNIQUE,          -- scheduled time (ISO, UTC) this digest answers
    generated_at TEXT NOT NULL,         -- when the queries finished (RSS pubDate)
    title TEXT NOT NULL,
    html TEXT NOT NULL,                 -- e-mail body
    data TEXT NOT NULL                  -- JSON: rows, keys, errors (for the Excel and the next diff)
);
"""


class DigestStore:
    def __init__(self, path: str | Path = ALERTS_DB) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def has_slot(self, slot: datetime) -> bool:
        with self.lock:
            return self.db.execute("SELECT 1 FROM digest WHERE slot = ?", (slot.isoformat(),)).fetchone() is not None

    def save(self, slot: datetime, generated_at: datetime, title: str, data: dict) -> int:
        """New digest without its HTML yet (the HTML links to the id); see set_html."""
        with self.lock, self.db:
            self.db.execute("DELETE FROM digest WHERE slot = ?", (slot.isoformat(),))
            cur = self.db.execute(
                "INSERT INTO digest (slot, generated_at, title, html, data) VALUES (?,?,?,'',?)",
                (slot.isoformat(), generated_at.isoformat(), title, json.dumps(data, ensure_ascii=False)))
            return cur.lastrowid

    def set_html(self, digest_id: int, html: str) -> None:
        with self.lock, self.db:
            self.db.execute("UPDATE digest SET html = ? WHERE id = ?", (html, digest_id))

    def latest(self, limit: int = 10) -> list[dict]:
        with self.lock:
            rows = self.db.execute("SELECT * FROM digest ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def get(self, digest_id: int) -> dict | None:
        with self.lock:
            r = self.db.execute("SELECT * FROM digest WHERE id = ?", (digest_id,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["data"] = json.loads(d["data"])
        d["generated_at"] = datetime.fromisoformat(d["generated_at"])
        d["slot"] = datetime.fromisoformat(d["slot"])
        return d
