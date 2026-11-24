"""Sheet "Resumo por Aeroporto": Airport summary."""
from __future__ import annotations

from collections import Counter

from ....domain.impact import LOW, SEVERITY_ORDER
from ...formatting import SEVERITIES, fmt_date_list, flight_key, fmt_pattern, describe, worst_severity, zone_of
from ..context import ReportContext
from ..styles import FILL, write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    res, aps, ops_per_airport = ctx.res, ctx.aps, ctx.ops_per_airport
    ws = wb.create_sheet("Resumo por Aeroporto")
    head = ["Aeroporto", "IATA", "Nome", "Operações na malha (dep+arr)", "Operações impactadas", "% impactadas",
            "Decolagens impactadas", "Pousos impactados", "CRITICAL", "HIGH", "MEDIUM", "LOW", "Pior severidade",
            "Dias com impacto", "Datas", "NOTAMs de pista vigentes", "NOTAMs e horários (local)"]
    rows = []
    for ap in sorted(res.runway_notams):
        rns = res.runway_notams[ap]
        if not rns:
            continue
        a = aps.get(ap)
        ops: dict[tuple, str] = {}
        for i in res.impacts:
            if i.airport == ap:
                key = (flight_key(i), i.side)
                ops[key] = worst_severity([ops.get(key, LOW), i.severity])
        sc = Counter(ops.values())
        days = sorted({k[0][2] for k in ops})
        total = ops_per_airport.get(ap, 0)
        rows.append([
            ap, a.iata if a else "", a.name if a else "", total, len(ops),
            round(100 * len(ops) / total, 1) if total else 0,
            sum(1 for k in ops if k[1] == "ORIGIN"), sum(1 for k in ops if k[1] == "DESTINATION"),
            *[sc.get(s, 0) for s in SEVERITIES], worst_severity(ops.values()) if ops else "", len(days), fmt_date_list(days),
            len(rns),
            "\n".join(f"{rn.notam.number} [{rn.severity}] {describe(rn)} — {fmt_pattern(rn.intervals, zone_of(aps, ap))}"
                      for rn in sorted(rns, key=lambda r: SEVERITY_ORDER[r.severity])),
        ])
    rows.sort(key=lambda r: (-r[4], r[0]))
    write_table(ws, head, rows, widths={3: 28, 15: 30, 17: 100}, sev_cols=(13,), wrap_cols=(3, 15, 17))
    for row in ws.iter_rows(min_row=2):
        for col, sev in zip(range(8, 12), SEVERITIES):
            if row[col].value:
                row[col].fill = FILL[sev]
