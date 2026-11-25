"""Public endpoints used by the web page (identificador de NOTAM), under /web/*. No API key.

    GET  /web/notams                 NOTAMs per airport, Brazil (AISWEB) and abroad (FAA) (same as /api/notams)
    POST /web/check                  is there a usable runway at one moment? (at most 20 legs)
    POST /web/schedule               send a schedule (raw body, header X-Filename): full analysis, Excel/CSV, history
    GET  /web/reports/{id}.{ext}     download the Excel/CSV of an analysis sent through the page
    GET  /web/changes                what changed between an analysis and the previous one
    GET  /web/alertas.rss            RSS: twice-a-day digest of every active NOTAM (Power Automate -> e-mail)
    GET  /web/resumos/{id}[.xlsx]    one digest as a page or Excel
    GET  /web/inbox?scope=br|intl    NOTAMs of the watched airports: unread / read / all (team-wide read marks)
    PUT|DELETE /web/inbox/{id}/read  mark / unmark as read
    POST /web/inbox/sync             query every watched airport now (besides 09:00/16:00)
    GET|POST /web/airports, DELETE /web/airports/{icao}   the watched airports

The integration API for other systems (/api/*) stays behind NOTAM_API_KEY.
"""
from __future__ import annotations

import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from starlette.concurrency import run_in_threadpool

from ..config import RUNS_DB, WEB_REPORTS_DIR
from ..domain.airport import BR_PREFIXES
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
from ..storage.alerts import DigestStore
from ..storage.notam_db import NotamDB
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
                 reports_dir: str | Path = WEB_REPORTS_DIR, digests: DigestStore | None = None,
                 notam_db: NotamDB | None = None) -> APIRouter:
    router = APIRouter(prefix="/web", tags=["web"])
    reports = Path(reports_dir)

    if digests is not None:
        _digest_routes(router, digests)
    if notam_db is not None:
        _inbox_routes(router, notam_db, source, airports)

    @router.get("/notams", response_model=s.NotamsResponse)
    def notams(icao: list[str] = Query(...), start: datetime | None = Query(None, alias="from"),
               end: datetime | None = Query(None, alias="to"), categories: list[str] | None = Query(None),
               include_other: bool = Query(False)):
        """Same as GET /api/notams, without key (used by the page)."""
        return query_notams(source, airports, icao, start, end, categories, include_other)

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


def _base(request: Request) -> str:
    from .digest import public_url
    return public_url() or str(request.base_url).rstrip("/") + "/"


def _digest_xlsx(d: dict) -> bytes:
    import io

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "NOTAMs"
    head = ["Aeroporto", "Nome", "Fonte", "NOTAM", "Novo", "Pista", "Tipo", "Em vigor", "Início (UTC)",
            "Fim (UTC)", "Horário (D)", "Texto"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    red = PatternFill("solid", fgColor="FDECEC")
    for r in d["data"]["rows"]:
        ws.append([r["icao"], r["name"], r["source"], r["number"], "sim" if r.get("new") else "",
                   "sim" if r["runway"] else "", r["kind"], "sim" if r["in_force"] else "futuro",
                   (r["valid_from"] or "")[:16].replace("T", " "),
                   "PERM" if r["permanent"] else (r["valid_to"] or "")[:16].replace("T", " ") + (" EST" if r["estimated"] else ""),
                   r["schedule"], r["text"]])
        if r["runway"]:
            for c in ws[ws.max_row]:
                c.fill = red
    for col, width in zip("ABCDEFGHIJKL", (9, 28, 8, 11, 6, 6, 26, 9, 16, 16, 24, 90)):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    if d["data"]["errors"]:
        e = wb.create_sheet("Sem dados")
        e.append(["Aeroporto", "Motivo"])
        for k, v in sorted(d["data"]["errors"].items()):
            e.append([k, v])
    if d["data"]["gone"]:
        g = wb.create_sheet("Saíram")
        g.append(["Aeroporto", "NOTAM", "Texto"])
        for r in d["data"]["gone"]:
            g.append([r["icao"], r["number"], r["text"]])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _digest_routes(router: APIRouter, digests: DigestStore) -> None:
    from .digest import render_rss

    @router.get("/alertas.rss", response_class=Response, responses={200: {"content": {"application/rss+xml": {}}}})
    def alerts_feed(request: Request):
        """The last digests as RSS. Each item is one e-mail: every active NOTAM of the airport list."""
        return Response(render_rss(digests, _base(request)), media_type="application/rss+xml; charset=utf-8")

    @router.get("/resumos/{name}", response_class=Response)
    def digest_view(name: str):
        """A digest as a page (/web/resumos/12) or as Excel (/web/resumos/12.xlsx)."""
        stem, _, ext = name.partition(".")
        d = digests.get(int(stem)) if stem.isdigit() else None
        if d is None or ext not in ("", "xlsx"):
            raise HTTPException(404, "Resumo não encontrado.")
        if ext == "xlsx":
            return Response(_digest_xlsx(d), headers={"Content-Disposition": f'attachment; filename="notams_{d["id"]}.xlsx"'},
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        page = (f'<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" '
                f'content="width=device-width,initial-scale=1"><title>Resumo de NOTAMs {d["id"]}</title></head>'
                f'<body style="margin:16px;background:#fff"><p><a href="{d["id"]}.xlsx">Baixar em Excel</a> · '
                f'<a href="../../">Identificador de NOTAM</a></p>{d["html"]}</body></html>')
        return Response(page, media_type="text/html; charset=utf-8")


SYNC_MIN_INTERVAL = timedelta(minutes=2)    # the button is public: do not let it hammer the AISWEB/FAA


def _inbox_notam(r: dict) -> s.InboxNotam:
    return s.InboxNotam(**{k: r[k] for k in s.InboxNotam.model_fields})


def _inbox_routes(router: APIRouter, db: NotamDB, source: NotamSource, airports: AirportDB) -> None:
    from threading import Lock

    from .digest import sync_notams

    sync_lock = Lock()
    last_sync: list[datetime] = []

    def in_scope(icao: str, scope: str | None) -> bool:
        return scope is None or (scope == "br") == icao.startswith(BR_PREFIXES)

    @router.get("/inbox", response_model=s.InboxResponse)
    def inbox(state: s.InboxState = Query("unread"), icao: str | None = Query(None),
              runway_only: bool = Query(False),
              scope: s.InboxScope | None = Query(None, description="br: Brazilian airports, intl: foreign ones")):
        """Active NOTAMs of the watched airports. Read marks are shared by the whole team."""
        code = icao.strip().upper() if icao else None
        watched = [a for a in db.airports() if in_scope(a["icao"], scope)]
        icaos = [a["icao"] for a in watched] if scope else None
        rows = db.notams(state, code, runway_only, icaos=icaos)
        per: dict[str, list[int]] = {}
        for r in db.notams("all", icaos=icaos):
            c = per.setdefault(r["icao"], [0, 0])
            c[1] += 1
            c[0] += r["read_at"] is None
        aps = [s.InboxAirport(icao=a["icao"], name=a["name"], last_sync_at=a["last_sync_at"], last_error=a["last_error"],
                              unread=per.get(a["icao"], [0, 0])[0], total=per.get(a["icao"], [0, 0])[1])
               for a in watched]
        return s.InboxResponse(state=state, counts=s.InboxCounts(**db.counts(code, icaos=icaos)), airports=aps,
                               notams=[_inbox_notam(r) for r in rows])

    @router.put("/inbox/{notam_id}/read", response_model=s.InboxNotam)
    def mark_read(notam_id: int, req: s.ReadRequest):
        r = db.mark_read(notam_id, req.by.strip(), datetime.now(UTC))
        if r is None:
            raise HTTPException(404, "NOTAM não encontrado.")
        return _inbox_notam(r)

    @router.delete("/inbox/{notam_id}/read", response_model=s.InboxNotam)
    def mark_unread(notam_id: int):
        r = db.mark_unread(notam_id)
        if r is None:
            raise HTTPException(404, "NOTAM não encontrado.")
        return _inbox_notam(r)

    @router.post("/inbox/sync", response_model=s.SyncInfo)
    async def sync_now():
        """Query every watched airport now. At most once every 2 minutes."""
        now = datetime.now(UTC)
        if last_sync and now - last_sync[-1] < SYNC_MIN_INTERVAL:
            raise HTTPException(429, "Atualizado há menos de 2 minutos. Aguarde um pouco.")
        if not sync_lock.acquire(blocking=False):
            raise HTTPException(409, "Já existe uma atualização em andamento.")
        try:
            last_sync[:] = [now]
            rows, result, icaos = await run_in_threadpool(sync_notams, db, source, airports, now)
        finally:
            sync_lock.release()
        return s.SyncInfo(synced_at=now, airports=len(icaos), notams=len(rows), new=len(result.new),
                          ended=len(result.ended), errors=result.errors)

    @router.get("/airports", response_model=list[s.InboxAirport])
    def watched(scope: s.InboxScope | None = Query(None)):
        return inbox("all", None, False, scope).airports

    @router.post("/airports", response_model=list[s.InboxAirport])
    def add_airport(req: s.AirportAdd):
        code = req.icao.strip().upper()
        if len(code) == 3 and code in airports.by_iata:
            code = airports.by_iata[code].icao
        if len(code) != 4 or not code.isalpha():
            raise HTTPException(422, f"Código inválido: {req.icao}. Use ICAO (SBSV) ou IATA (SSA).")
        if not in_scope(code, req.scope):
            where = "NOTAMs Brasil" if req.scope == "intl" else "NOTAMs internacionais"
            kind = "brasileiro" if req.scope == "intl" else "fora do Brasil"
            raise HTTPException(422, f"{code} é {kind}: adicione na aba \"{where}\".")
        ap = airports.by_icao.get(code)
        db.add_airport(code, ap.name if ap else "", datetime.now(UTC))
        return watched(req.scope)

    @router.delete("/airports/{icao}", response_model=list[s.InboxAirport])
    def remove_airport(icao: str, scope: s.InboxScope | None = Query(None)):
        if not db.remove_airport(icao.strip().upper()):
            raise HTTPException(404, "Aeroporto não está na lista.")
        return watched(scope)
