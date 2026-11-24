"""Sheet "Matriz Dia x Aeroporto": Day x airport matrix."""
from __future__ import annotations

from datetime import date

from openpyxl.utils import get_column_letter

from ....domain.impact import LOW, SEVERITY_ORDER
from ...formatting import SHORT, WEEKDAYS_PT, flight_key
from ..context import ReportContext
from ..styles import FILL, style_header


def add_sheet(wb, ctx: ReportContext) -> None:
    res, dates = ctx.res, ctx.dates
    ws = wb.create_sheet("Matriz Dia x Aeroporto")
    airports = sorted({i.airport for i in res.impacts})
    cells: dict[tuple[str, date], list] = {}
    for i in res.impacts:
        cats, sev, fl = cells.setdefault((i.airport, i.flight.date), [set(), LOW, set()])
        cats.add(i.rn.cls.primary)
        fl.add(flight_key(i))
        if SEVERITY_ORDER[i.severity] < SEVERITY_ORDER[sev]:
            cells[(i.airport, i.flight.date)][1] = i.severity
    ws.append(["Aeroporto"] + [f"{d:%d/%m} {WEEKDAYS_PT[d.weekday()]}" for d in dates] + ["Total voos"])
    style_header(ws[1])
    for ap in airports:
        row, total = [ap], set()
        for d in dates:
            if (ap, d) in cells:
                cats, _, fl = cells[(ap, d)]
                total |= fl
                row.append(f"{' / '.join(SHORT.get(c, c) for c in sorted(cats))} ({len(fl)})")
            else:
                row.append("")
        row.append(len(total))
        ws.append(row)
        r = ws.max_row
        for j, d in enumerate(dates, start=2):
            if (ap, d) in cells:
                ws.cell(r, j).fill = FILL[cells[(ap, d)][1]]
    ws.column_dimensions["A"].width = 12
    for j in range(2, len(dates) + 3):
        ws.column_dimensions[get_column_letter(j)].width = 14
    ws.freeze_panes = "B2"
    ws.append([])
    ws.append(["Legenda: célula = categoria (nº de voos impactados no dia); cor = pior severidade. "
               "COMPR = redução de comprimento/distâncias declaradas; RWY FECH = pista fechada; AD FECH = aeródromo "
               "fechado; LARG = largura reduzida; SUPERF = condição da superfície/obras."])
