"""Sheet "Novidades": what changed since the previous recorded run (new, ended and modified impacts)."""
from __future__ import annotations

from datetime import datetime

from openpyxl.styles import Alignment

from ....domain.changes import ENDED, NEW, SEVERITY_CHANGED, WINDOW_CHANGED
from ...formatting import SIDE_PT, WEEKDAYS_PT
from ..context import ReportContext
from ..styles import FILL, TITLE_FONT, style_header

HEAD = ["Mudança", "Data", "Dia sem.", "Voo", "Origem", "Destino", "Lado", "Aeroporto", "NOTAM", "Categoria",
        "Severidade anterior", "Severidade atual", "Janela anterior (UTC)", "Janela atual (UTC)"]
WIDTHS = [20, 12, 9, 10, 9, 9, 9, 10, 12, 20, 12, 12, 30, 30]


def _window(r) -> str:
    if r is None:
        return ""
    s, e = datetime.fromisoformat(r.window_start), datetime.fromisoformat(r.window_end)
    return f"{s:%d/%m %H:%M} → {e:%d/%m %H:%M}"


def add_sheet(wb, ctx: ReportContext) -> None:
    ws = wb.create_sheet("Novidades")
    ws.append(["O que mudou desde a última execução"])
    ws["A1"].font = TITLE_FONT
    cs = ctx.changes
    if cs is None:
        ws.append(["Histórico desligado nesta execução (modo offline ou --no-history): nada foi comparado."])
        return
    if cs.previous_run is None:
        ws.append(["Primeira execução registrada: a próxima mostrará os impactos novos, encerrados e alterados."])
        return
    prev = cs.previous_run
    ws.append([f"Comparado com a execução de {datetime.fromisoformat(prev['started_at']):%d/%m/%Y %H:%M} "
               f"(malha {prev.get('schedule_file') or '?'}). Novos: {cs.count(NEW)} | "
               f"severidade mudou: {cs.count(SEVERITY_CHANGED)} | janela mudou: {cs.count(WINDOW_CHANGED)} | "
               f"encerrados: {cs.count(ENDED)}"])
    ws.append([])
    ws.append(HEAD)
    style_header(ws[ws.max_row])
    header_row = ws.max_row
    for c in cs.changes:
        r = c.record
        d = datetime.fromisoformat(r.date)
        ws.append([c.kind, f"{d:%d/%m/%Y}", WEEKDAYS_PT[d.weekday()], r.flight, r.origin, r.destination,
                   SIDE_PT.get(r.side, r.side), r.airport, r.notam, r.category,
                   c.previous.severity if c.previous else "", c.current.severity if c.current else "",
                   _window(c.previous), _window(c.current)])
        for col in (11, 12):
            cell = ws.cell(ws.max_row, col)
            if cell.value in FILL:
                cell.fill = FILL[cell.value]
        for cell in ws[ws.max_row]:
            cell.alignment = Alignment(vertical="top")
    for i, w in enumerate(WIDTHS, start=1):
        ws.column_dimensions[ws.cell(header_row, i).column_letter].width = w
    ws.freeze_panes = ws.cell(header_row + 1, 1)
    if cs.changes:
        ws.auto_filter.ref = f"A{header_row}:{ws.cell(ws.max_row, len(HEAD)).coordinate}"
