"""Sheet "Janelas Ativas": Activity windows."""
from __future__ import annotations

from datetime import datetime

from ...formatting import WEEKDAYS_PT, fmt_duration, flight_key, fmt_flight_list, fmt_dt, describe, zone_of
from ..context import ReportContext
from ..styles import write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    aps, tz_of, by_rn, all_rns, rn_airport = ctx.aps, ctx.tz_of, ctx.by_rn, ctx.all_rns, ctx.rn_airport
    ws = wb.create_sheet("Janelas Ativas")
    head = ["Aeroporto", "NOTAM", "Severidade", "O que é", "Data (local)", "Dia sem.", "Início (local)",
            "Fim (local)", "Início (UTC)", "Fim (UTC)", "Duração", "Voos afetados", "Voos (DEP/ARR hora local)"]
    rows = []
    for rn in all_rns:
        ap = rn_airport[id(rn)]
        tz = zone_of(aps, ap)
        items = by_rn.get(id(rn), [])
        for s, e in rn.intervals:
            hit = [i for i in items if any(ws_ < e and we > s for ws_, we in i.windows)]
            s2 = s.astimezone(tz)
            rows.append([ap, rn.notam.number, rn.severity, describe(rn), s2.strftime("%d/%m/%Y"),
                         WEEKDAYS_PT[s2.weekday()], fmt_dt(s, tz), fmt_dt(e, tz), fmt_dt(s), fmt_dt(e), fmt_duration(s, e),
                         len({flight_key(i) for i in hit}), fmt_flight_list(hit, tz_of)])
    rows.sort(key=lambda r: (datetime.strptime(r[8], "%d/%m/%Y %H:%M"), r[0]))
    write_table(ws, head, rows, widths={4: 45, 13: 70}, sev_cols=(3,), wrap_cols=(4, 13))
