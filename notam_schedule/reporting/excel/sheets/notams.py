"""Sheet "Resumo por NOTAM": NOTAM summary."""
from __future__ import annotations

from ....domain.categories import DESCRIPTION
from ....domain.impact import SEVERITY_ORDER
from ...formatting import (
    CLOSURE_HEAD, closure_cells, describe, flight_key, fmt_date_list, fmt_flight_list, fmt_pattern, fmt_validity, zone_of,
)
from ....domain.confidence import notam_confidence
from ..context import ReportContext
from ..styles import write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    aps, tz_of, by_rn, all_rns, rn_airport = ctx.aps, ctx.tz_of, ctx.by_rn, ctx.all_rns, ctx.rn_airport
    ws = wb.create_sheet("Resumo por NOTAM")
    head = ["Aeroporto", "NOTAM", "Severidade", "Categoria", "Pista", "O que é", "Quando (hora local)",
            "Janelas no período", "Horas ativas no período", "Voos afetados", "Decolagens", "Pousos",
            "Dias com voo afetado", "Voos afetados (data voo DEP/ARR hora local)", "Vigência (B→C) UTC",
            "Período (D)", "D interpretado?", "Texto (E)", "Observação", "Confiabilidade", "Confiab. (pontos)", "Motivos da confiabilidade",
            *CLOSURE_HEAD]
    rows = []
    for rn in sorted(all_rns, key=lambda r: (SEVERITY_ORDER[r.severity], rn_airport[id(r)])):
        ap = rn_airport[id(rn)]
        items = by_rn.get(id(rn), [])
        conf = notam_confidence(rn)
        hours = sum((e - s).total_seconds() for s, e in rn.intervals) / 3600
        rows.append([
            ap, rn.notam.number, rn.severity, DESCRIPTION.get(rn.cls.primary, rn.cls.primary),
            ", ".join(rn.cls.runways), describe(rn), fmt_pattern(rn.intervals, zone_of(aps, ap)), len(rn.intervals),
            round(hours, 1), len({flight_key(i) for i in items}),
            len({flight_key(i) for i in items if i.side == "ORIGIN"}),
            len({flight_key(i) for i in items if i.side == "DESTINATION"}),
            fmt_date_list([i.flight.date for i in items]), fmt_flight_list(items, tz_of), fmt_validity(rn.notam),
            rn.notam.d, "sim" if rn.d_parsed else "NÃO", rn.notam.e, rn.note, conf.level, conf.score, conf.explanation,
            *closure_cells(rn),
        ])
    write_table(ws, head, rows, widths={6: 45, 7: 50, 13: 30, 14: 70, 15: 34, 16: 30, 18: 70, 19: 40, 22: 70},
                 sev_cols=(3,), wrap_cols=(6, 7, 13, 14, 16, 18, 19, 22))
