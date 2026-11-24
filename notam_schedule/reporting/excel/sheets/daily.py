"""Sheet "Resumo por Dia": Daily summary."""
from __future__ import annotations

from collections import Counter, defaultdict

from ...formatting import SEVERITIES, SHORT, WEEKDAYS_PT, flight_key
from ..context import ReportContext
from ..styles import FILL, write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    by_flight, flight_worst, flights_per_day, dates, by_day_flights = ctx.by_flight, ctx.flight_worst, ctx.flights_per_day, ctx.dates, ctx.by_day_flights
    ws = wb.create_sheet("Resumo por Dia")
    head = ["Data", "Dia sem.", "Voos na malha", "Voos impactados", "% impactados", "CRITICAL", "HIGH", "MEDIUM",
            "LOW", "Decolagens impactadas", "Pousos impactados", "NOTAMs que afetam voos", "Aeroportos (voos)"]
    rows = []
    for d in dates:
        ks = by_day_flights.get(d, set())
        day_imp = [i for k in ks for i in by_flight[k]]
        sc = Counter(flight_worst[k] for k in ks)
        ap_cnt = Counter(i.airport for i in {(flight_key(i), i.side): i for i in day_imp}.values())
        ap_cats: dict[str, set] = defaultdict(set)
        for i in day_imp:
            ap_cats[i.airport].add(SHORT.get(i.rn.cls.primary, i.rn.cls.primary))
        rows.append([
            d.strftime("%d/%m/%Y"), WEEKDAYS_PT[d.weekday()], flights_per_day[d], len(ks),
            round(100 * len(ks) / flights_per_day[d], 1) if flights_per_day[d] else 0,
            *[sc.get(s, 0) for s in SEVERITIES],
            len({flight_key(i) for i in day_imp if i.side == "ORIGIN"}),
            len({flight_key(i) for i in day_imp if i.side == "DESTINATION"}),
            ", ".join(sorted({f"{i.airport} {i.rn.notam.number}" for i in day_imp})),
            "; ".join(f"{ap} {'/'.join(sorted(ap_cats[ap]))} ({n})" for ap, n in sorted(ap_cnt.items())),
        ])
    write_table(ws, head, rows, widths={12: 50, 13: 70}, wrap_cols=(12, 13))
    for row in ws.iter_rows(min_row=2):
        for col, sev in zip(range(5, 9), SEVERITIES):
            if row[col].value:
                row[col].fill = FILL[sev]
