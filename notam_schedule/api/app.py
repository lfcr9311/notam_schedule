"""HTTP API: NOTAMs per airport and runway checks for flight legs.

Sources: AISWEB (DECEA) for Brazilian airports, FAA NMS-API for the rest of the world (when configured).

    GET  /api/health          liveness (no AISWEB/FAA call)
    GET  /api/notams          runway closures (or every NOTAM) of one or more airports in a time window
    POST /api/runway-check    which closures apply to each flight leg (batch, for other systems)
    GET  /                    web page (identificador de NOTAM); its public endpoints are in web.py (/web/*)

Configuration (environment):
    AISWEB_API_KEY / AISWEB_API_PASS   AISWEB credentials (or .env in the project root)
    FAA_NMS_CLIENT_ID / _SECRET        FAA NMS-API credentials; without them airports outside Brazil are out of coverage
    FAA_NMS_API_URL / FAA_NMS_AUTH_URL FAA environment (default: production api-nms.aim.faa.gov)
    NOTAM_API_KEY                      when set, every /api/* call except /api/health needs header X-API-Key
    NOTAM_OFFLINE_DIR                  read <ICAO>.xml / faa_<ICAO>.json instead of calling the APIs (tests, demos)
    NOTAM_CACHE_MIN                    AISWEB/FAA response cache in minutes (default 60)
    NOTAM_ROOT_PATH                    path prefix behind the proxy (e.g. /notam), so /docs works there
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .. import __version__
from ..config import CACHE_DIR, DEFAULT_BASE_URL, RAW_DIR, AiswebConfig, FaaConfig, load_dotenv
from ..domain.categories import AERODROME_CLOSED, CATEGORIES, DESCRIPTION, RUNWAY_CLOSED
from ..domain.confidence import impact_confidence, notam_confidence
from ..domain.impact import SEVERITY_ORDER, Impact, leg_restrictions
from ..domain.impact import RunwayNotam as DomainRunwayNotam
from ..domain.ports import NotamSource, covers, source_name
from ..domain.qcode import extract_qcode
from ..ingestion.airports import AirportDB
from ..ingestion.sources import build_source
from ..pipelines.analyze import OtherNotam, collect_notams, collect_runway_notams, split_coverage
from ..reporting.formatting import describe
from ..storage.raw_archive import FileRawArchive
from . import schemas as s

UTC = timezone.utc

if TYPE_CHECKING:
    from ..storage.notam_db import NotamDB
DEFAULT_CATEGORIES = [RUNWAY_CLOSED, AERODROME_CLOSED]
DEFAULT_DAYS, MAX_DAYS, MAX_AIRPORTS = 15, 62, 50
OPERATION_SIDE = {"departure": "ORIGIN", "arrival": "DESTINATION"}
OPS = {"": "both", "LDG": "landing", "TKOF": "takeoff"}


def default_source() -> tuple[NotamSource, str]:
    """AISWEB (+ FAA when configured) from the environment, or offline files when NOTAM_OFFLINE_DIR is set."""
    load_dotenv()
    ttl = int(os.getenv("NOTAM_CACHE_MIN", "60"))
    offline = os.getenv("NOTAM_OFFLINE_DIR", "").strip()
    if offline:
        return build_source(None, offline_dir=offline), "offline"
    key, pwd = os.getenv("AISWEB_API_KEY", "").strip(), os.getenv("AISWEB_API_PASS", "").strip()
    if not key or not pwd:
        raise RuntimeError("AISWEB_API_KEY/AISWEB_API_PASS não configuradas (ou defina NOTAM_OFFLINE_DIR).")
    config = AiswebConfig(key, pwd, os.getenv("AISWEB_BASE_URL", DEFAULT_BASE_URL))
    source = build_source(config, FaaConfig.from_env(), cache_dir=CACHE_DIR, cache_ttl_min=ttl,
                          archive=FileRawArchive(RAW_DIR))
    return source, "aisweb"


def _outside_message(source: NotamSource) -> str:
    intl = getattr(source, "international", None)
    if intl is None:
        return "Fora do Brasil: a consulta internacional (FAA) não está configurada no servidor."
    if getattr(intl, "offline_dir", None) is not None:
        return "Modo offline (arquivos de exemplo): não há resposta da FAA salva para este aeroporto."
    return "Sem fonte de NOTAM para este código."


def _categories(values: list[str] | None) -> set[str] | None:
    """None/[] -> closures only; ["all"] -> every category (returned as None = no filter)."""
    if not values:
        return set(DEFAULT_CATEGORIES)
    flat = {v.strip().upper() for item in values for v in item.split(",") if v.strip()}
    if flat == {"ALL"}:
        return None
    invalid = flat - set(CATEGORIES)
    if invalid:
        raise HTTPException(422, f"Categorias inválidas: {', '.join(sorted(invalid))}. Use {', '.join(CATEGORIES)} ou all.")
    return flat


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _confidence(c) -> s.Confidence:
    return s.Confidence(level=c.level, score=c.score, reasons=c.reasons)


def _closure(rn: DomainRunwayNotam) -> s.Closure | None:
    c = rn.cls
    if c.primary not in (RUNWAY_CLOSED, AERODROME_CLOSED):
        return None
    return s.Closure(type="conditional" if c.conditional else "total", operations=OPS[c.closed_ops],
                     exception=c.exception or None)


def _periods(intervals) -> list[s.Period]:
    return [s.Period(start=a, end=b) for a, b in intervals]


def _notam(rn: DomainRunwayNotam, icao: str) -> s.RunwayNotam:
    n, c = rn.notam, rn.cls
    return s.RunwayNotam(
        id=f"{icao}|{n.number}", number=n.number, airport=icao, category=c.primary,
        category_label=DESCRIPTION.get(c.primary, c.primary), description=describe(rn), severity=rn.severity,
        runways=c.runways, closure=_closure(rn), valid_from=n.start, valid_to=n.end, permanent=n.permanent,
        valid_to_estimated=n.end_estimated, schedule=n.d, schedule_understood=rn.d_parsed,
        active_periods=_periods(rn.intervals), text=n.e, qcode=extract_qcode(n.raw), detection=c.source,
        ml_probability=c.prob, confidence=_confidence(notam_confidence(rn)), note=rn.note, duplicates=rn.duplicates)


def _other(o: OtherNotam, icao: str) -> s.OtherNotam:
    n = o.notam
    return s.OtherNotam(
        id=f"{icao}|{n.number}", number=n.number, airport=icao, qcode=extract_qcode(n.raw), valid_from=n.start,
        valid_to=n.end, permanent=n.permanent, valid_to_estimated=n.end_estimated, schedule=n.d,
        schedule_understood=o.d_parsed, active_periods=_periods(o.intervals), text=n.e, note=o.note)


def query_notams(source: NotamSource, airports: AirportDB, icao: list[str], start: datetime | None,
                 end: datetime | None, categories: list[str] | None, include_other: bool = False) -> s.NotamsResponse:
    """Shared by GET /api/notams (with key) and GET /web/notams (web page).

    include_other: also return every NOTAM that is not a runway restriction (other_notams)."""
    codes = sorted({c.strip().upper() for item in icao for c in item.split(",") if c.strip()})
    bad = [c for c in codes if len(c) != 4 or not c.isalpha()]
    if bad or not codes:
        raise HTTPException(422, f"Código ICAO inválido: {', '.join(bad) or '(vazio)'}.")
    if len(codes) > MAX_AIRPORTS:
        raise HTTPException(422, f"No máximo {MAX_AIRPORTS} aeroportos por consulta.")
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    w_start = _utc(start) if start else now
    w_end = _utc(end) if end else w_start + timedelta(days=DEFAULT_DAYS)
    if w_end <= w_start or w_end - w_start > timedelta(days=MAX_DAYS):
        raise HTTPException(422, f"Janela inválida: 'to' deve ser depois de 'from' e no máximo {MAX_DAYS} dias.")
    cats = _categories(categories)
    query, outside = split_coverage(codes, source)
    got = collect_notams(source, query, w_start, w_end, cats, keep_other=include_other)
    found, errors = got.runway, got.errors
    out = []
    for code in codes:
        ap = airports.by_icao.get(code)
        info = dict(icao=code, name=ap.name if ap else None, timezone=ap.tz if ap else None,
                    source=source_name(source, code) if code in query else None)
        if code in outside:
            out.append(s.AirportNotams(**info, status="out_of_coverage", error=_outside_message(source)))
        elif code in errors:
            out.append(s.AirportNotams(**info, status="error", error=errors[code]))
        else:
            rns = sorted(found.get(code, []), key=lambda r: (SEVERITY_ORDER[r.severity], r.notam.start or w_start))
            rwys = next((r.airport_runways for r in rns if r.airport_runways), None) or source.runways(code)
            out.append(s.AirportNotams(
                **info, status="ok", runways=[s.Runway(ident=r.ident, length_m=r.length, width_m=r.width) for r in rwys],
                notams=[_notam(rn, code) for rn in rns],
                other_notams=[_other(o, code) for o in sorted(got.other.get(code, []),
                                                               key=lambda o: o.notam.start or w_start)]))
    return s.NotamsResponse(generated_at=datetime.now(UTC), window=s.Period(start=w_start, end=w_end),
                            categories=sorted(cats) if cats else ["all"], airports=out)


def check_legs(source: NotamSource, req: s.RunwayCheckRequest) -> s.RunwayCheckResponse:
    """Shared by POST /api/runway-check (with key) and POST /web/check (web page)."""
    cats = _categories(req.categories)
    margin = timedelta(minutes=req.margin_minutes)
    times = [_utc(leg.time) for leg in req.legs]
    w_start, w_end = min(times) - margin - timedelta(days=1), max(times) + margin + timedelta(days=1)
    query, outside = split_coverage((leg.airport for leg in req.legs), source)
    found, _, errors = collect_runway_notams(source, query, w_start, w_end, cats)
    published: dict[str, list[str] | None] = {}
    results = []
    for leg, t in zip(req.legs, times):
        base = dict(id=leg.id, airport=leg.airport, operation=leg.operation, time=t)
        if leg.airport in outside or leg.airport in errors:
            status = "out_of_coverage" if leg.airport in outside else "error"
            results.append(s.LegResult(**base, status=status,
                                       error=errors.get(leg.airport, _outside_message(source)),
                                       restricted=False, no_runway=False))
            continue
        rns = found.get(leg.airport, [])
        side = OPERATION_SIDE[leg.operation]
        hits, open_rwys, no_runway = leg_restrictions(rns, side, t - margin, t + margin)
        if not hits:  # nothing closed: every published runway is open
            if leg.airport not in published:
                rwys = next((r.airport_runways for r in rns if r.airport_runways), None) or source.runways(leg.airport)
                published[leg.airport] = [r.ident for r in rwys] or None
            open_rwys = published[leg.airport]
        impacts = [Impact(None, side, leg.airport, t, rn, w, open_rwys, no_runway) for rn, w in hits]
        notams_out = [s.LegNotam(
            id=f"{leg.airport}|{i.rn.notam.number}", number=i.rn.notam.number, category=i.rn.cls.primary,
            description=describe(i.rn), severity=i.severity, runways=i.rn.cls.runways, closure=_closure(i.rn),
            active_periods=_periods(i.windows), confidence=_confidence(impact_confidence(i))) for i in impacts]
        worst = min((i.severity for i in impacts), key=SEVERITY_ORDER.get, default=None)
        results.append(s.LegResult(**base, status="ok", restricted=bool(impacts), severity=worst,
                                   no_runway=no_runway, open_runways=open_rwys, notams=notams_out))
    return s.RunwayCheckResponse(generated_at=datetime.now(UTC), margin_minutes=req.margin_minutes,
                                 categories=sorted(cats) if cats else ["all"], legs=results)


STATIC = Path(__file__).parent / "static"


def create_app(source: NotamSource | None = None, source_kind: str = "aisweb", api_key: str | None = None,
               airports: AirportDB | None = None, runs_db: str | Path | None = None,
               reports_dir: str | Path | None = None, alerts_db: str | Path | None = None,
               schedule_digest: bool | None = None, notam_db: "NotamDB | None" = None) -> FastAPI:
    """source/api_key default to the environment (see module docstring); tests inject them.

    schedule_digest: build the twice-a-day NOTAM digest in a background thread (default: on when the source comes
    from the environment and NOTAM_DIGEST is not 'off'; tests leave it off and call POST /api/resumos).
    notam_db: watched airports + NOTAMs + read marks (default: DATABASE_URL, else SQLite in data/)."""
    from contextlib import asynccontextmanager

    from ..config import ALERTS_DB
    from ..storage.alerts import DigestStore
    from ..storage.notam_db import NotamDB
    from .digest import DigestScheduler, build_digest, public_url
    from .web import build_router

    if schedule_digest is None:
        schedule_digest = source is None and os.getenv("NOTAM_DIGEST", "on").strip().lower() != "off"
    if source is None:
        source, source_kind = default_source()
    if api_key is None:
        api_key = os.getenv("NOTAM_API_KEY", "").strip() or None
    airports = airports or AirportDB.default()

    digests = DigestStore(alerts_db or ALERTS_DB)
    db = notam_db or NotamDB()                 # table `airport` = the watched airports (managed on the page)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        scheduler = DigestScheduler(digests, db, source, airports, public_url()) if schedule_digest else None
        if scheduler:
            scheduler.start()
        yield
        if scheduler:
            scheduler.stop.set()

    app = FastAPI(title="NOTAM Schedule API", version=__version__, lifespan=lifespan,
                  description="NOTAMs (AISWEB/DECEA no Brasil, FAA NMS-API no exterior): fechamentos de pista por "
                              "aeroporto, todos os NOTAMs e checagem de voos.")

    def require_key(x_api_key: str | None = Header(None, description="Required when the server sets NOTAM_API_KEY")):
        if api_key and not (x_api_key and secrets.compare_digest(x_api_key, api_key)):
            raise HTTPException(401, "Chave de API ausente ou inválida (cabeçalho X-API-Key).")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/health", response_model=s.Health, tags=["status"])
    def health():
        """Liveness check. Does not call the AISWEB or the FAA."""
        return s.Health(status="ok", version=__version__, source=source_kind, international=covers(source, "KJFK"))

    @app.get("/api/notams", response_model=s.NotamsResponse, tags=["notams"], dependencies=[Depends(require_key)])
    def notams(
        icao: list[str] = Query(..., description="One or more ICAO codes (repeat the parameter or use commas)",
                                examples=["SBSV,SBBE"]),
        start: datetime | None = Query(None, alias="from", description="Window start (UTC). Default: now"),
        end: datetime | None = Query(None, alias="to", description=f"Window end (UTC). Default: start + {DEFAULT_DAYS} days"),
        categories: list[str] | None = Query(None, description="Default: RUNWAY_CLOSED,AERODROME_CLOSED; 'all' = every category"),
        include_other: bool = Query(False, description="Also return every non-runway NOTAM in other_notams"),
    ):
        """Runway NOTAMs active in the window, per airport, with closure type, periods and confidence.

        Brazilian airports come from the AISWEB; the others from the FAA NMS-API (when the server has its
        credentials). Airports without a source or whose query failed come back with status out_of_coverage/error:
        that means there is NO DATA for them, not that they have no restriction.
        """
        return query_notams(source, airports, icao, start, end, categories, include_other)

    @app.post("/api/runway-check", response_model=s.RunwayCheckResponse, tags=["notams"],
              dependencies=[Depends(require_key)])
    def runway_check(req: s.RunwayCheckRequest):
        """For each flight leg, the closures that apply to that operation within ±margin of its time.

        Combines every closure at the airport: `no_runway` is true when, at some moment of the window, all
        runways published in ROTAER are closed (then `severity` is CRITICAL even if each NOTAM alone is not).
        A landing-only closure does not restrict departures, and vice versa.
        """
        return check_legs(source, req)

    @app.post("/api/resumos", response_model=s.DigestInfo, tags=["resumos"], dependencies=[Depends(require_key)])
    def digest_now(request: Request):
        """Build a NOTAM digest now (besides the scheduled 09:00/16:00), e.g. to test the Power Automate flow."""
        now = datetime.now(UTC).replace(second=0, microsecond=0)
        base = public_url() or str(request.base_url).rstrip("/") + "/"
        d = digests.get(build_digest(digests, db, source, airports, now, base))
        return s.DigestInfo(id=d["id"], title=d["title"], generated_at=d["generated_at"],
                            page=f"{base}web/resumos/{d['id']}", feed=f"{base}web/alertas.rss")

    paths = {k: v for k, v in (("runs_db", runs_db), ("reports_dir", reports_dir)) if v is not None}
    app.include_router(build_router(source, airports, digests=digests, notam_db=db, **paths))
    return app
