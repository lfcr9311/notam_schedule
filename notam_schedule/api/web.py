"""Public endpoints used by the web page (identificador de NOTAM), under /web/*. No API key.

    GET  /web/notams                 runway NOTAMs per airport (same as /api/notams)
    POST /web/check                  is there a usable runway at one moment? (at most 20 legs)
    POST /web/schedule               send a schedule (raw body, header X-Filename): full analysis, Excel/CSV, history
    GET  /web/reports/{id}.{ext}     download the Excel/CSV of an analysis sent through the page
    GET  /web/changes                what changed between an analysis and the previous one

The integration API for other systems (/api/*) stays behind NOTAM_API_KEY.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ..config import RUNS_DB, WEB_REPORTS_DIR
from ..domain.changes import ENDED, NEW, SEVERITY_CHANGED, WINDOW_CHANGED, Change, diff_impacts
from ..domain.confidence import impact_confidence
from ..domain.impact import CRITICAL, SEVERITY_ORDER, daily_summary
from ..domain.ports import NotamSource
from ..ingestion.airports import AirportDB
from ..ingestion.schedule import ScheduleError, load_schedule
from ..pipelines.analyze import analyze
from ..pipelines.history import record_run
from ..reporting.csv_export import write_csv
from ..reporting.excel import write_excel
from ..reporting.formatting import describe
from ..storage.runs import RunStore
from . import schemas as s
from .app import OPERATION_SIDE, _categories, _closure, _confidence, check_legs, query_notams

UTC = timezone.utc
MAX_UPLOAD = 20 * 1024 * 1024
MAX_WEB_LEGS = 20
MAX_IMPACT_ROWS = 2000
MAX_CHANGE_ROWS = 1000
KEEP_REPORTS = 30
SIDE_OPERATION = {v: k for k, v in OPERATION_SIDE.items()}
KIND_COUNT = {NEW: "new", SEVERITY_CHANGED: "severity_changed", WINDOW_CHANGED: "window_changed", ENDED: "ended"}


def _run_info(run: dict | None) -> s.RunInfo | None:
    if not run:
        return None
    return s.RunInfo(id=run["id"], started_at=datetime.fromisoformat(run["started_at"]),
                     schedule_file=run.get("schedule_file"), period_start=run.get("period_start"),
                     period_end=run.get("period_end"), flights=run.get("flights"), impacts=run.get("impacts"))


def _window(r) -> s.Period | None:
    if r is None:
        return None
    return s.Period(start=datetime.fromisoformat(r.window_start), end=datetime.fromisoformat(r.window_end))


def _change_row(c: Change) -> s.ChangeRow:
    r = c.record
    return s.ChangeRow(kind=c.kind, date=r.date, flight=r.flight, origin=r.origin, destination=r.destination,
                       operation=SIDE_OPERATION[r.side], airport=r.airport, notam=r.notam, category=r.category,
                       severity_before=c.previous.severity if c.previous else None,
                       severity_after=c.current.severity if c.current else None,
                       window_before=_window(c.previous), window_after=_window(c.current))


def _changes_response(store: RunStore, run: dict | None, previous: dict | None, changes: list[Change]) -> s.ChangesResponse:
    counts = s.ChangeCounts(**{KIND_COUNT[k]: sum(c.kind == k for c in changes) for k in KIND_COUNT})
    return s.ChangesResponse(run=_run_info(run), previous=_run_info(previous), counts=counts,
                             changes=[_change_row(c) for c in changes[:MAX_CHANGE_ROWS]],
                             runs=[_run_info(r) for r in store.runs(20)])


def _prune_reports(folder: Path) -> None:
    """Keep only the most recent KEEP_REPORTS analyses (Excel + CSV each)."""
    files = sorted(folder.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[KEEP_REPORTS:]:
        old.unlink(missing_ok=True)
        old.with_suffix(".csv").unlink(missing_ok=True)


def build_router(source: NotamSource, airports: AirportDB, runs_db: str | Path = RUNS_DB,
                 reports_dir: str | Path = WEB_REPORTS_DIR) -> APIRouter:
    router = APIRouter(prefix="/web", tags=["web"])
    reports = Path(reports_dir)

    @router.get("/notams", response_model=s.NotamsResponse)
    def notams(icao: list[str] = Query(...), start: datetime | None = Query(None, alias="from"),
               end: datetime | None = Query(None, alias="to"), categories: list[str] | None = Query(None)):
        """Same as GET /api/notams, without key (used by the page)."""
        return query_notams(source, airports, icao, start, end, categories)

    @router.post("/check", response_model=s.RunwayCheckResponse)
    def check(req: s.RunwayCheckRequest):
        """Same as POST /api/runway-check, limited to a few legs (the page checks one moment at a time)."""
        if len(req.legs) > MAX_WEB_LEGS:
            raise HTTPException(422, f"No máximo {MAX_WEB_LEGS} horários por consulta.")
        return check_legs(source, req)

    @router.post("/schedule", response_model=s.ScheduleResult)
    async def schedule(request: Request, x_filename: str = Header("malha.xlsx"),
                       margin_before: int = Query(60, ge=0, le=360), margin_after: int = Query(60, ge=0, le=360),
                       categories: list[str] | None = Query(None),
                       date_from: date | None = Query(None, description="First flight date (default: all)"),
                       date_to: date | None = Query(None, description="Last flight date (default: all)")):
        """Full schedule analysis, like the command line: Excel/CSV to download and the run kept in the history."""
        data = await request.body()
        name = Path(unquote(x_filename)).name or "malha.xlsx"
        if not data:
            raise HTTPException(400, "O arquivo está vazio.")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(413, "O arquivo é maior que 20 MB.")
        suffix = Path(name).suffix.lower()
        if suffix not in (".csv", ".xlsx", ".xlsm"):
            raise HTTPException(400, "Envie a malha em .csv ou .xlsx.")
        cats = _categories(categories)
        # the analysis may take a minute (one AISWEB query per airport): keep the event loop free
        return await run_in_threadpool(_analyze, data, name, suffix, margin_before, margin_after, cats,
                                       date_from, date_to)

    def _analyze(data: bytes, name: str, suffix: str, margin_before: int, margin_after: int,
                 cats: set[str] | None, date_from: date | None, date_to: date | None) -> s.ScheduleResult:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"malha{suffix}"
            path.write_bytes(data)
            try:
                sched = load_schedule(path, airports, local_times=None, date_from=date_from, date_to=date_to)
            except ScheduleError as exc:
                raise HTTPException(400, f"Erro na malha: {exc}")
            if not sched.flights:
                raise HTTPException(400, "Nenhum voo encontrado na malha para o período informado.")
            res = analyze(sched.flights, source, margin_before, margin_after, cats)
            params = {"Malha": name, "Horários da malha": sched.time_basis,
                      "Margem (min) antes/depois": f"{margin_before}/{margin_after}",
                      "Categorias": "todas" if cats is None else ", ".join(sorted(cats)),
                      "Fonte NOTAM": "API AISWEB", "Detector": "rules", "Origem": "página web"}
            store = RunStore(runs_db)
            changes = record_run(store, res, path, "rules", params, name=name)
        reports.mkdir(parents=True, exist_ok=True)
        write_excel(res, reports / f"{changes.run_id}.xlsx", params, changes)
        write_csv(res, reports / f"{changes.run_id}.csv")
        _prune_reports(reports)

        impacts = sorted(res.impacts, key=lambda i: (SEVERITY_ORDER[i.severity], i.flight.date,
                                                     i.reference_utc or datetime.min.replace(tzinfo=UTC)))
        rows = []
        for i in impacts[:MAX_IMPACT_ROWS]:
            f, ap = i.flight, (i.flight.origin if i.side == "ORIGIN" else i.flight.destination)
            rows.append(s.ImpactRow(
                date=f.date.isoformat(), flight=f.flight, origin=f.origin.icao or f.origin.iata,
                destination=f.destination.icao or f.destination.iata, operation=SIDE_OPERATION[i.side],
                airport=i.airport, timezone=ap.tz, time=i.reference_utc, severity=i.severity,
                category=i.rn.cls.primary, description=describe(i.rn), notam=i.rn.notam.number,
                closure=_closure(i.rn), open_runways=i.open_runways, no_runway=i.no_runway,
                confidence=_confidence(impact_confidence(i))))
        fkey = lambda i: (i.flight.row, i.flight.flight, i.flight.date)  # noqa: E731
        days = [s.DaySummary(date=d["date"].isoformat(), flights=d["flights"], impacted=d["impacted"],
                             critical=d["critical"], airports=sorted(d["airports"])) for d in daily_summary(res)]
        dates = [f.date for f in res.flights]
        prev = store.previous(changes.run_id)
        return s.ScheduleResult(
            run_id=changes.run_id, schedule_file=name, time_basis=sched.time_basis,
            period=s.Period(start=datetime.combine(min(dates), datetime.min.time(), UTC),
                            end=datetime.combine(max(dates), datetime.max.time(), UTC)),
            categories=sorted(cats) if cats else ["all"], flights=len(res.flights),
            impacted_flights=len({fkey(i) for i in res.impacts}),
            critical_flights=len({fkey(i) for i in res.impacts if i.severity == CRITICAL}),
            by_day=days, impacts=rows, impacts_total=len(res.impacts), errors=res.errors,
            out_of_coverage=res.out_of_coverage,
            changes=_changes_response(store, store.get(changes.run_id), prev, changes.changes),
            downloads=s.Downloads(xlsx=f"web/reports/{changes.run_id}.xlsx", csv=f"web/reports/{changes.run_id}.csv"))

    @router.get("/reports/{run_id}.{ext}", include_in_schema=True)
    def report(run_id: int, ext: str):
        """Excel or CSV of an analysis sent through the page (the latest ones are kept)."""
        if ext not in ("xlsx", "csv"):
            raise HTTPException(404, "Formato não encontrado.")
        path = reports / f"{run_id}.{ext}"
        if not path.is_file():
            raise HTTPException(404, "Relatório não encontrado (só os mais recentes ficam guardados).")
        media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if ext == "xlsx"
                 else "text/csv")
        return FileResponse(path, media_type=media, filename=f"notam-impacto-{run_id}.{ext}")

    @router.get("/changes", response_model=s.ChangesResponse)
    def changes(run_id: int | None = Query(None, description="Default: the latest run")):
        """What changed in a run compared with the one before it."""
        store = RunStore(runs_db)
        run = store.get(run_id) if run_id is not None else (store.runs(1) or [None])[0]
        if run_id is not None and run is None:
            raise HTTPException(404, "Análise não encontrada.")
        if run is None:
            return _changes_response(store, None, None, [])
        prev = store.previous(run["id"])
        if prev is None:
            return _changes_response(store, run, None, [])
        period = (date.fromisoformat(run["period_start"]), date.fromisoformat(run["period_end"]))
        cs = diff_impacts(store.records(prev["id"]), store.records(run["id"]), period, prev)
        return _changes_response(store, run, prev, cs.changes)

    return router
