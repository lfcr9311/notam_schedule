"""Watched airports and their NOTAMs in a SQL database (PostgreSQL in production, SQLite locally/tests).

The analysts mark NOTAMs as read for the whole team (who and when). A NOTAM is identified by airport + number:
a replacement (NOTAMR) has a new number, so it comes in unread; the replaced one leaves the active list
(active = false, ended_at) and stays in the table as history.

    DATABASE_URL   e.g. postgresql+psycopg://user:pass@host:5432/notams (default: SQLite in data/notams.db)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, MetaData, String, Table, Text, create_engine, func, select, update,
)
from sqlalchemy.engine import Engine

from ..config import NOTAM_DB_URL

UTC = timezone.utc
meta = MetaData()

airport_t = Table(
    "airport", meta,
    Column("icao", String(4), primary_key=True),
    Column("name", String(120), nullable=False, default=""),
    Column("active", Boolean, nullable=False, default=True),
    Column("added_at", DateTime(timezone=True), nullable=False),
    Column("last_sync_at", DateTime(timezone=True)),
    Column("last_error", Text),
)

notam_t = Table(
    "notam", meta,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("key", String(40), nullable=False, unique=True),     # ICAO|number
    Column("icao", String(4), nullable=False, index=True),
    Column("number", String(20), nullable=False),
    Column("source", String(10), nullable=False, default=""),
    Column("runway", Boolean, nullable=False, default=False),   # runway/aerodrome restriction
    Column("kind", String(80), nullable=False, default=""),
    Column("severity", String(10), nullable=False, default=""),
    Column("runways", String(80), nullable=False, default=""),
    Column("valid_from", DateTime(timezone=True)),
    Column("valid_to", DateTime(timezone=True)),
    Column("permanent", Boolean, nullable=False, default=False),
    Column("estimated", Boolean, nullable=False, default=False),
    Column("schedule", Text, nullable=False, default=""),
    Column("text", Text, nullable=False, default=""),
    Column("active", Boolean, nullable=False, default=True, index=True),
    Column("first_seen", DateTime(timezone=True), nullable=False),
    Column("last_seen", DateTime(timezone=True), nullable=False),
    Column("ended_at", DateTime(timezone=True)),
    Column("read_at", DateTime(timezone=True)),
    Column("read_by", String(80)),
)

FIELDS = ("number", "source", "runway", "kind", "severity", "runways", "valid_from", "valid_to", "permanent",
          "estimated", "schedule", "text")


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip() or NOTAM_DB_URL


def _utc(dt: datetime | None) -> datetime | None:
    """SQLite gives naive datetimes back: they were stored in UTC."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _dt(value) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return _utc(value)
    return _utc(datetime.fromisoformat(value))


@dataclass
class SyncResult:
    new: list[str] = field(default_factory=list)       # keys first seen in this sync
    ended: list[dict] = field(default_factory=list)    # NOTAMs that left the active list
    errors: dict[str, str] = field(default_factory=dict)


class NotamDB:
    def __init__(self, url: str | None = None, engine: Engine | None = None) -> None:
        url = url or database_url()
        if engine is None:
            if url.startswith("sqlite:///"):
                from pathlib import Path
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, pool_pre_ping=True, future=True)
        self.engine = engine
        meta.create_all(self.engine)

    # ---------------------------------------------------------------- airports
    def seed_airports(self, airports: list[tuple[str, str]], now: datetime) -> int:
        """Insert the (icao, name) not in the table yet. Existing ones (even deactivated) are left alone."""
        with self.engine.begin() as c:
            have = {r[0] for r in c.execute(select(airport_t.c.icao))}
            new = [dict(icao=i, name=n or "", active=True, added_at=now) for i, n in airports if i not in have]
            if new:
                c.execute(airport_t.insert(), new)
        return len(new)

    def add_airport(self, icao: str, name: str, now: datetime) -> None:
        with self.engine.begin() as c:
            if c.execute(select(airport_t.c.icao).where(airport_t.c.icao == icao)).first():
                c.execute(update(airport_t).where(airport_t.c.icao == icao).values(active=True, name=name or ""))
            else:
                c.execute(airport_t.insert().values(icao=icao, name=name or "", active=True, added_at=now))

    def remove_airport(self, icao: str) -> bool:
        """Stop watching (the NOTAM history stays)."""
        with self.engine.begin() as c:
            return c.execute(update(airport_t).where(airport_t.c.icao == icao).values(active=False)).rowcount > 0

    def airports(self, active_only: bool = True) -> list[dict]:
        q = select(airport_t).order_by(airport_t.c.icao)
        if active_only:
            q = q.where(airport_t.c.active.is_(True))
        with self.engine.connect() as c:
            rows = [dict(r._mapping) for r in c.execute(q)]
        for r in rows:
            r["added_at"], r["last_sync_at"] = _utc(r["added_at"]), _utc(r["last_sync_at"])
        return rows

    # ---------------------------------------------------------------- sync
    def sync_airport(self, icao: str, rows: list[dict], now: datetime) -> tuple[list[str], list[dict]]:
        """Store the airport's current NOTAMs. Returns (new keys, NOTAMs that left the active list)."""
        values = {r["key"]: {**{k: r.get(k) for k in FIELDS}, "runways": ",".join(r.get("runways") or []),
                             "valid_from": _dt(r.get("valid_from")), "valid_to": _dt(r.get("valid_to"))}
                  for r in rows}
        with self.engine.begin() as c:
            known = {r.key: r for r in c.execute(select(notam_t.c.key, notam_t.c.id, notam_t.c.active)
                                                 .where(notam_t.c.icao == icao))}
            new = [k for k in values if k not in known]
            if new:
                c.execute(notam_t.insert(), [dict(key=k, icao=icao, active=True, first_seen=now, last_seen=now,
                                                  **values[k]) for k in new])
            for k, v in values.items():
                if k in known:
                    c.execute(update(notam_t).where(notam_t.c.id == known[k].id)
                              .values(active=True, last_seen=now, ended_at=None, **v))
            gone_ids = [r.id for k, r in known.items() if r.active and k not in values]
            ended = []
            if gone_ids:
                ended = [dict(r._mapping) for r in c.execute(select(notam_t).where(notam_t.c.id.in_(gone_ids)))]
                c.execute(update(notam_t).where(notam_t.c.id.in_(gone_ids)).values(active=False, ended_at=now))
            c.execute(update(airport_t).where(airport_t.c.icao == icao).values(last_sync_at=now, last_error=None))
        return new, ended

    def sync_error(self, icao: str, error: str, now: datetime) -> None:
        """The query failed: keep the NOTAMs as they were (never mark them ended without data)."""
        with self.engine.begin() as c:
            c.execute(update(airport_t).where(airport_t.c.icao == icao).values(last_error=error[:500]))

    # ---------------------------------------------------------------- reading / read marks
    def notams(self, state: str = "all", icao: str | None = None, runway_only: bool = False,
               active: bool = True, icaos: list[str] | None = None) -> list[dict]:
        """state: unread | read | all. Runway restrictions first, then newest first. icaos: limit to these."""
        q = select(notam_t).where(notam_t.c.icao.in_(self._watched()))   # removed airports leave the list
        if icaos is not None:
            q = q.where(notam_t.c.icao.in_(icaos))
        if active:
            q = q.where(notam_t.c.active.is_(True))
        if state == "unread":
            q = q.where(notam_t.c.read_at.is_(None))
        elif state == "read":
            q = q.where(notam_t.c.read_at.is_not(None))
        if icao:
            q = q.where(notam_t.c.icao == icao)
        if runway_only:
            q = q.where(notam_t.c.runway.is_(True))
        q = q.order_by(notam_t.c.icao, notam_t.c.runway.desc(), notam_t.c.first_seen.desc(), notam_t.c.number)
        with self.engine.connect() as c:
            return [self._row(r) for r in c.execute(q)]

    def counts(self, icao: str | None = None, icaos: list[str] | None = None) -> dict[str, int]:
        q = select(func.count(), func.count(notam_t.c.read_at)).where(
            notam_t.c.active.is_(True), notam_t.c.icao.in_(self._watched()))
        if icaos is not None:
            q = q.where(notam_t.c.icao.in_(icaos))
        if icao:
            q = q.where(notam_t.c.icao == icao)
        with self.engine.connect() as c:
            total, read = c.execute(q).one()
        return {"all": total, "read": read, "unread": total - read}

    @staticmethod
    def _watched():
        return select(airport_t.c.icao).where(airport_t.c.active.is_(True)).scalar_subquery()

    def get(self, notam_id: int) -> dict | None:
        with self.engine.connect() as c:
            r = c.execute(select(notam_t).where(notam_t.c.id == notam_id)).first()
        return self._row(r) if r else None

    def mark_read(self, notam_id: int, by: str, now: datetime) -> dict | None:
        with self.engine.begin() as c:
            c.execute(update(notam_t).where(notam_t.c.id == notam_id).values(read_at=now, read_by=by[:80]))
        return self.get(notam_id)

    def mark_unread(self, notam_id: int) -> dict | None:
        with self.engine.begin() as c:
            c.execute(update(notam_t).where(notam_t.c.id == notam_id).values(read_at=None, read_by=None))
        return self.get(notam_id)

    @staticmethod
    def _row(r) -> dict:
        d = dict(r._mapping)
        for k in ("valid_from", "valid_to", "first_seen", "last_seen", "ended_at", "read_at"):
            d[k] = _utc(d[k])
        d["runways"] = [x for x in (d["runways"] or "").split(",") if x]
        return d
