"""Twice-a-day NOTAM digest for the analysts, published as RSS (GET /web/alertas.rss) for Power Automate.

At each scheduled time (default 09:00 and 16:00 Brasília) every watched airport (database table `airport`) is
queried — AISWEB for Brazil, FAA abroad — and stored (table `notam`, where the analysts mark them as read); ALL active
NOTAMs (in force now or starting later) go into one digest: one RSS item = one e-mail.
NOTAMs first seen in this sync are highlighted and the ones that left are listed; airports whose query
failed are reported as "sem dados" (never as "no NOTAM"). pubDate = when the queries finished, so a poller that
already checked the feed after the scheduled time still sees it as new.

    NOTAM_ALERT_AIRPORTS   comma-separated list; default: data/reference/alert_airports.txt (one code per line)
    NOTAM_DIGEST_TIMES     default 09:00,16:00
    NOTAM_DIGEST_TZ        default America/Sao_Paulo
    NOTAM_PUBLIC_URL       public address of the page, for the links in the e-mail (e.g. http://host/notam/)
"""
from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from html import escape
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo

from ..config import ALERT_AIRPORTS_FILE
from ..domain.ports import NotamSource
from ..ingestion.airports import AirportDB
from ..storage.alerts import DigestStore
from ..storage.notam_db import NotamDB, SyncResult
from .app import MAX_AIRPORTS, MAX_DAYS, query_notams

log = logging.getLogger(__name__)
UTC = timezone.utc
DEFAULT_AIRPORTS = ["SBSV", "SBBE"]
CATCH_UP = timedelta(hours=3)        # after a restart, still build a digest whose time passed less than this ago
FEED_ITEMS = 10


# ---------------------------------------------------------------- configuration
def digest_zone() -> ZoneInfo:
    return ZoneInfo(os.getenv("NOTAM_DIGEST_TZ", "America/Sao_Paulo"))


def digest_times() -> list[tuple[int, int]]:
    out = []
    for item in os.getenv("NOTAM_DIGEST_TIMES", "09:00,16:00").split(","):
        h, _, m = item.strip().partition(":")
        out.append((int(h), int(m or 0)))
    return sorted(out)


def alert_airports(airports: AirportDB) -> list[str]:
    """Initial list of watched airports (seeds the database): NOTAM_ALERT_AIRPORTS, else the list file, else
    SBSV/SBBE. IATA codes are converted. After that the database is the list (add/remove on the page)."""
    raw = os.getenv("NOTAM_ALERT_AIRPORTS", "")
    if raw.strip():
        codes = raw.replace(";", ",").split(",")
    elif ALERT_AIRPORTS_FILE.is_file():
        codes = [line.split("#")[0] for line in ALERT_AIRPORTS_FILE.read_text(encoding="utf-8").splitlines()]
    else:
        codes = DEFAULT_AIRPORTS
    out: list[str] = []
    for c in (c.strip().upper() for c in codes):
        if len(c) == 3 and c in airports.by_iata:
            c = airports.by_iata[c].icao
        if len(c) == 4 and c.isalpha() and c not in out:
            out.append(c)
        elif c:
            log.warning("Código de aeroporto ignorado na lista de alertas: %s", c)
    return out


def slots_between(start: datetime, end: datetime) -> list[datetime]:
    """Scheduled digest times (UTC) in (start, end]."""
    zone, out = digest_zone(), []
    day = start.astimezone(zone).date()
    while day <= end.astimezone(zone).date():
        for h, m in digest_times():
            t = datetime(day.year, day.month, day.day, h, m, tzinfo=zone).astimezone(UTC)
            if start < t <= end:
                out.append(t)
        day += timedelta(days=1)
    return out


# ---------------------------------------------------------------- building
def _fmt(dt: datetime | None) -> str:
    return f"{dt.astimezone(UTC):%d/%m/%Y %H:%M}" if dt else "?"


def collect_rows(source: NotamSource, airports: AirportDB, icaos: list[str], now: datetime) -> tuple[list[dict], dict]:
    """Every active NOTAM of the airports as flat rows, plus {icao: error} for airports without data."""
    rows: list[dict] = []
    errors: dict[str, str] = {}
    for i in range(0, len(icaos), MAX_AIRPORTS):
        res = query_notams(source, airports, [",".join(icaos[i:i + MAX_AIRPORTS])], now,
                           now + timedelta(days=MAX_DAYS), ["all"], include_other=True)
        for a in res.airports:
            if a.status != "ok":
                errors[a.icao] = a.error or a.status
                continue
            base = dict(icao=a.icao, name=a.name or "", source=a.source or "")
            for n in a.notams:
                rows.append(dict(base, number=n.number, runway=True, kind=n.category_label + (
                    " (condicional/PPR)" if n.closure and n.closure.type == "conditional" else ""),
                    severity=n.severity, runways=n.runways, **_common(n)))
            for n in a.other_notams:
                rows.append(dict(base, number=n.number, runway=False, kind=f"Q{n.qcode}" if n.qcode else "Outro",
                                 severity="", runways=[], **_common(n)))
    unique: dict[str, dict] = {}
    for r in rows:
        r["key"] = f"{r['icao']}|{r['number']}"
        r["in_force"] = not r["valid_from"] or datetime.fromisoformat(r["valid_from"]) <= now
        unique.setdefault(r["key"], r)          # same NOTAM twice in a response: keep the first (runway first)
    return list(unique.values()), errors


def _common(n) -> dict:
    return dict(valid_from=n.valid_from.isoformat() if n.valid_from else None,
                valid_to=n.valid_to.isoformat() if n.valid_to else None, permanent=n.permanent,
                estimated=n.valid_to_estimated, schedule=n.schedule, text=n.text)


def _validity(r: dict) -> str:
    start = _fmt(datetime.fromisoformat(r["valid_from"])) if r["valid_from"] else "?"
    end = "PERM" if r["permanent"] else _fmt(datetime.fromisoformat(r["valid_to"])) if r["valid_to"] else "?"
    return f"{start} → {end}{' EST' if r['estimated'] else ''}"


TD = 'style="border:1px solid #d0d7de;padding:4px 6px;vertical-align:top;font-size:12px"'
TH = 'style="border:1px solid #d0d7de;padding:4px 6px;background:#f0f3f6;text-align:left;font-size:12px"'
NEW = '<span style="background:#ffe08a;color:#5c4400;padding:1px 5px;border-radius:3px;font-weight:bold">NOVO</span> '
RWY = 'style="background:#fdecec"'


def _table(rows: list[dict], with_airport: bool = False) -> str:
    head = (["Aeroporto"] if with_airport else []) + ["NOTAM", "Tipo", "Vigência (UTC)", "Horário (D)", "Texto"]
    out = ['<table style="border-collapse:collapse;width:100%">', "<tr>" + "".join(f"<th {TH}>{h}</th>" for h in head) + "</tr>"]
    for r in rows:
        kind = escape(r["kind"]) + ("" if r["in_force"] else "<br><i>futuro</i>")
        cells = ([escape(r["icao"])] if with_airport else []) + [
            (NEW if r.get("new") else "") + f"<b>{escape(r['number'])}</b>", kind, escape(_validity(r)),
            escape(r["schedule"] or "—"), escape(r["text"]).replace("\n", "<br>")]
        out.append(f"<tr {RWY if r['runway'] else ''}>" + "".join(f"<td {TD}>{c}</td>" for c in cells) + "</tr>")
    return "".join(out) + "</table>"


def max_email_bytes() -> int:
    return int(os.getenv("NOTAM_DIGEST_MAX_KB", "500")) * 1024


def render_html(rows: list[dict], errors: dict, gone: list[dict], icaos: list[str], when_local: datetime,
                link: str, compact: bool = False) -> str:
    """compact: only runway NOTAMs in full; the others as counts (the full list stays on the page/Excel)."""
    new = [r for r in rows if r.get("new")]
    rwy = sum(r["runway"] for r in rows)
    p = ['<div style="font-family:Segoe UI,Arial,sans-serif;font-size:13px;color:#1f2328">',
         f"<p><b>Resumo de NOTAMs · {when_local:%d/%m/%Y %H:%M} (Brasília)</b><br>"
         f"{len(icaos)} aeroportos · {len(rows)} NOTAMs ativos ({rwy} de pista) · "
         f"<b>{len(new)} novos</b> · {len(gone)} saíram desde o envio anterior</p>"]
    if errors:
        p.append('<p style="background:#fff1e5;border:1px solid #f0b37e;padding:8px">'
                 "<b>Sem dados de NOTAM (consulta falhou ou fora da cobertura) — não significa ausência de NOTAM:</b><br>"
                 + "<br>".join(f"{escape(k)}: {escape(v)}" for k, v in sorted(errors.items())) + "</p>")
    if new:
        p += [f"<h3>Novos desde o envio anterior ({len(new)})</h3>", _table(new, with_airport=True)]
    if gone:
        p += [f"<h3>Saíram desde o envio anterior ({len(gone)})</h3>",
              "<p>" + ", ".join(f"{escape(g['icao'])} {escape(g['number'])}" for g in gone) + "</p>"]
    if compact:
        p.append(f'<p style="background:#eef4ff;border:1px solid #9ec2ff;padding:8px">A lista completa é grande demais '
                 f'para o e-mail: aqui vão só as restrições de pista. <a href="{escape(link)}"><b>Todos os NOTAMs na página '
                 f'e em Excel</b></a>.</p>')
    p.append("<h3>" + ("Restrições de pista por aeroporto" if compact else "Todos os NOTAMs ativos por aeroporto") + "</h3>"
             "<p>Linhas em vermelho: restrições de pista. Horários em UTC.</p>")
    for icao in icaos:
        if icao in errors:
            continue
        mine = sorted((r for r in rows if r["icao"] == icao), key=lambda r: (not r["runway"], not r.get("new"), r["number"]))
        name = mine[0]["name"] if mine else ""
        shown = [r for r in mine if r["runway"]] if compact else mine
        p.append(f"<h4>{escape(icao)} {escape(name)} · {len(mine)} NOTAMs"
                 + (f" ({len(shown)} de pista; os outros na página)" if compact else "") + "</h4>")
        p.append(_table(shown) if shown else "<p>Nenhum NOTAM ativo.</p>" if not mine else "<p>Nenhuma restrição de pista.</p>")
    p.append(f'<p><a href="{escape(link)}">Ver no identificador de NOTAM / baixar em Excel</a></p></div>')
    return "".join(p)


def sync_notams(db: NotamDB, source: NotamSource, airports: AirportDB, now: datetime) -> tuple[list[dict], SyncResult, list[str]]:
    """Query every watched airport and store the result. Returns (current rows, sync result, airports queried).

    rows[i]["new"]: first seen now, at an airport that had been synced before (the first sync of an airport
    is its baseline, not news). An airport whose query failed keeps its NOTAMs untouched."""
    watched = db.airports()
    icaos = [a["icao"] for a in watched]
    baseline = {a["icao"] for a in watched if a["last_sync_at"] is None}
    rows, errors = collect_rows(source, airports, icaos, now)
    result = SyncResult(errors=errors)
    by_icao: dict[str, list[dict]] = {i: [] for i in icaos}
    for r in rows:
        by_icao[r["icao"]].append(r)
    for icao in icaos:
        if icao in errors:
            db.sync_error(icao, errors[icao], now)
            continue
        new, ended = db.sync_airport(icao, by_icao[icao], now)
        if icao not in baseline:
            result.new += new
            result.ended += ended
    fresh = set(result.new)
    for r in rows:
        r["new"] = r["key"] in fresh
    return rows, result, icaos


def build_digest(store: DigestStore, db: NotamDB, source: NotamSource, airports: AirportDB, slot: datetime,
                 base_url: str, now: datetime | None = None) -> int:
    """Sync every watched airport into the database and store the e-mail digest. Returns the digest id."""
    started = now or datetime.now(UTC)
    rows, result, icaos = sync_notams(db, source, airports, started)
    errors = result.errors
    gone = [dict(icao=g["icao"], number=g["number"], key=g["key"], text=g["text"]) for g in result.ended]
    finished = now or datetime.now(UTC)          # pubDate: after the queries, never before a poll
    local = slot.astimezone(digest_zone())
    new = len(result.new)
    title = (f"NOTAMs {local:%d/%m %H:%M} · {new} {'novo' if new == 1 else 'novos'} · {len(rows)} ativos em "
             f"{len(icaos)} aeroporto{'' if len(icaos) == 1 else 's'}"
             + (f" · {len(errors)} sem dados" if errors else ""))
    digest_id = store.save(slot, finished, title, {"rows": rows, "errors": errors, "gone": gone, "airports": icaos})
    link = f"{base_url}web/resumos/{digest_id}"
    html = render_html(rows, errors, gone, icaos, local, link)
    if len(html.encode("utf-8")) > max_email_bytes():
        html = render_html(rows, errors, gone, icaos, local, link, compact=True)
    store.set_html(digest_id, html)
    log.info("Resumo %s: %d NOTAMs, %d novos, %d sem dados", digest_id, len(rows), new, len(errors))
    return digest_id


# ---------------------------------------------------------------- RSS
def render_rss(store: DigestStore, base_url: str, now: datetime | None = None) -> str:
    now = now or datetime.now(UTC)
    out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>',
           "<title>Resumo de NOTAMs (identificador de NOTAM)</title>", f"<link>{xml_escape(base_url)}</link>",
           "<description>Todos os NOTAMs ativos da lista de aeroportos, duas vezes ao dia.</description>",
           "<language>pt-br</language>", f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>"]
    for d in store.latest(FEED_ITEMS):
        if not d["html"]:
            continue
        out += ["<item>", f"<title>{xml_escape(d['title'])}</title>",
                f"<link>{xml_escape(f'{base_url}web/resumos/{d['id']}')}</link>",
                f'<guid isPermaLink="false">notam-digest-{d["id"]}-{d["slot"].isoformat()}</guid>',
                f"<pubDate>{format_datetime(d['generated_at'].astimezone(UTC))}</pubDate>",
                f"<description>{xml_escape(d['html'])}</description>", "</item>"]
    return "\n".join(out + ["</channel></rss>"]) + "\n"


# ---------------------------------------------------------------- scheduler
class DigestScheduler:
    """Background thread: builds the digest at each scheduled time (and catches up after a restart)."""

    def __init__(self, store: DigestStore, db: NotamDB, source: NotamSource, airports: AirportDB, base_url: str) -> None:
        self.store, self.db, self.source, self.airports, self.base_url = store, db, source, airports, base_url
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="notam-digest", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def due(self, now: datetime) -> datetime | None:
        """The most recent scheduled time not built yet (within CATCH_UP), if any."""
        pending = [s for s in slots_between(now - CATCH_UP, now) if not self.store.has_slot(s)]
        return pending[-1] if pending else None

    def _loop(self) -> None:
        while not self.stop.is_set():
            slot = self.due(datetime.now(UTC))
            if slot is not None:
                try:
                    build_digest(self.store, self.db, self.source, self.airports, slot, self.base_url)
                except Exception:          # never kill the thread: retry on the next tick
                    log.exception("Falha ao montar o resumo de NOTAMs de %s", slot)
                    self.stop.wait(300)
                    continue
            self.stop.wait(30)


def public_url() -> str:
    url = os.getenv("NOTAM_PUBLIC_URL", "").strip()
    return url.rstrip("/") + "/" if url else ""
