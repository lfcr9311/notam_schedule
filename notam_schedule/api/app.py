"""HTTP API: runway NOTAMs per airport and runway checks for flight legs.

    GET  /api/health          liveness (no AISWEB call)
    GET  /api/notams          runway closures of one or more airports in a time window
    POST /api/runway-check    which closures apply to each flight leg (batch, for other systems)

Configuration (environment):
    AISWEB_API_KEY / AISWEB_API_PASS   AISWEB credentials (or .env in the project root)
    NOTAM_API_KEY                      when set, every /api/* call except /api/health needs header X-API-Key
    NOTAM_OFFLINE_DIR                  read <ICAO>.xml files instead of calling the AISWEB (tests, demos)
    NOTAM_CACHE_MIN                    AISWEB response cache in minutes (default 60)
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query

from .. import __version__
from ..config import CACHE_DIR, DEFAULT_BASE_URL, RAW_DIR, AiswebConfig, load_dotenv
from ..domain.categories import AERODROME_CLOSED, CATEGORIES, DESCRIPTION, RUNWAY_CLOSED
from ..domain.confidence import impact_confidence, notam_confidence
from ..domain.impact import SEVERITY_ORDER, Impact, leg_restrictions
from ..domain.impact import RunwayNotam as DomainRunwayNotam
from ..domain.ports import NotamSource
from ..domain.qcode import extract_qcode
from ..ingestion.aisweb import AiswebClient
from ..pipelines.analyze import collect_runway_notams, split_coverage
from ..reporting.formatting import describe
from ..storage.raw_archive import FileRawArchive
from . import schemas as s

UTC = timezone.utc
DEFAULT_CATEGORIES = [RUNWAY_CLOSED, AERODROME_CLOSED]
DEFAULT_DAYS, MAX_DAYS, MAX_AIRPORTS = 15, 62, 50
OPERATION_SIDE = {"departure": "ORIGIN", "arrival": "DESTINATION"}
OPS = {"": "both", "LDG": "landing", "TKOF": "takeoff"}


def default_source() -> tuple[NotamSource, str]:
    """AISWEB client from the environment, or offline files when NOTAM_OFFLINE_DIR is set."""
    load_dotenv()
    ttl = int(os.getenv("NOTAM_CACHE_MIN", "60"))
    offline = os.getenv("NOTAM_OFFLINE_DIR", "").strip()
    if offline:
        return AiswebClient(None, offline_dir=offline), "offline"
    key, pwd = os.getenv("AISWEB_API_KEY", "").strip(), os.getenv("AISWEB_API_PASS", "").strip()
    if not key or not pwd:
        raise RuntimeError("AISWEB_API_KEY/AISWEB_API_PASS não configuradas (ou defina NOTAM_OFFLINE_DIR).")
    config = AiswebConfig(key, pwd, os.getenv("AISWEB_BASE_URL", DEFAULT_BASE_URL))
    return AiswebClient(config, cache_dir=CACHE_DIR, cache_ttl_min=ttl, archive=FileRawArchive(RAW_DIR)), "aisweb"


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


def create_app(source: NotamSource | None = None, source_kind: str = "aisweb", api_key: str | None = None) -> FastAPI:
    """source/api_key default to the environment (see module docstring); tests inject them."""
    if source is None:
        source, source_kind = default_source()
    if api_key is None:
        api_key = os.getenv("NOTAM_API_KEY", "").strip() or None

    app = FastAPI(title="NOTAM Schedule API", version=__version__,
                  description="NOTAMs de pista (AISWEB/DECEA): fechamentos por aeroporto e checagem de voos.")

    def require_key(x_api_key: str | None = Header(None, description="Required when the server sets NOTAM_API_KEY")):
        if api_key and not (x_api_key and secrets.compare_digest(x_api_key, api_key)):
            raise HTTPException(401, "Chave de API ausente ou inválida (cabeçalho X-API-Key).")

    @app.get("/api/health", response_model=s.Health, tags=["status"])
    def health():
        """Liveness check. Does not call the AISWEB."""
        return s.Health(status="ok", version=__version__, source=source_kind)

    @app.get("/api/notams", response_model=s.NotamsResponse, tags=["notams"], dependencies=[Depends(require_key)])
    def notams(
        icao: list[str] = Query(..., description="One or more ICAO codes (repeat the parameter or use commas)",
                                examples=["SBSV,SBBE"]),
        start: datetime | None = Query(None, alias="from", description="Window start (UTC). Default: now"),
        end: datetime | None = Query(None, alias="to", description=f"Window end (UTC). Default: start + {DEFAULT_DAYS} days"),
        categories: list[str] | None = Query(None, description="Default: RUNWAY_CLOSED,AERODROME_CLOSED; 'all' = every category"),
    ):
        """Runway NOTAMs active in the window, per airport, with closure type, periods and confidence.

        Airports outside DECEA coverage or whose query failed come back with status out_of_coverage/error:
        that means there is NO DATA for them, not that they have no restriction.
        """
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
        query, outside = split_coverage(codes)
        found, _, errors = collect_runway_notams(source, query, w_start, w_end, cats)
        airports = []
        for code in codes:
            if code in outside:
                airports.append(s.AirportNotams(icao=code, status="out_of_coverage",
                                                error="Fora da cobertura da AISWEB/DECEA."))
            elif code in errors:
                airports.append(s.AirportNotams(icao=code, status="error", error=errors[code]))
            else:
                rns = sorted(found.get(code, []), key=lambda r: (SEVERITY_ORDER[r.severity], r.notam.start or w_start))
                rwys = next((r.airport_runways for r in rns if r.airport_runways), None) or source.runways(code)
                airports.append(s.AirportNotams(
                    icao=code, status="ok", runways=[s.Runway(ident=r.ident, length_m=r.length, width_m=r.width)
                                                     for r in rwys],
                    notams=[_notam(rn, code) for rn in rns]))
        return s.NotamsResponse(generated_at=datetime.now(UTC), window=s.Period(start=w_start, end=w_end),
                                categories=sorted(cats) if cats else ["all"], airports=airports)

    @app.post("/api/runway-check", response_model=s.RunwayCheckResponse, tags=["notams"],
              dependencies=[Depends(require_key)])
    def runway_check(req: s.RunwayCheckRequest):
        """For each flight leg, the closures that apply to that operation within ±margin of its time.

        Combines every closure at the airport: `no_runway` is true when, at some moment of the window, all
        runways published in ROTAER are closed (then `severity` is CRITICAL even if each NOTAM alone is not).
        A landing-only closure does not restrict departures, and vice versa.
        """
        cats = _categories(req.categories)
        margin = timedelta(minutes=req.margin_minutes)
        times = [_utc(leg.time) for leg in req.legs]
        w_start, w_end = min(times) - margin - timedelta(days=1), max(times) + margin + timedelta(days=1)
        query, outside = split_coverage(leg.airport for leg in req.legs)
        found, _, errors = collect_runway_notams(source, query, w_start, w_end, cats)
        results = []
        for leg, t in zip(req.legs, times):
            base = dict(id=leg.id, airport=leg.airport, operation=leg.operation, time=t)
            if leg.airport in outside or leg.airport in errors:
                status = "out_of_coverage" if leg.airport in outside else "error"
                results.append(s.LegResult(**base, status=status, error=errors.get(leg.airport, "Fora da cobertura da AISWEB/DECEA."),
                                           restricted=False, no_runway=False))
                continue
            side = OPERATION_SIDE[leg.operation]
            hits, open_rwys, no_runway = leg_restrictions(found.get(leg.airport, []), side, t - margin, t + margin)
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

    return app
