"""Sheet "NOTAMs de Pista": Runway NOTAMs (full detail)."""
from __future__ import annotations

from ....domain.categories import DESCRIPTION
from ....domain.impact import SEVERITY_ORDER
from ...formatting import CLOSURE_HEAD, closure_cells, fmt_validity
from ....domain.confidence import notam_confidence
from ..context import ReportContext
from ..styles import write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    res = ctx.res
    ws = wb.create_sheet("NOTAMs de Pista")
    head = ["Aeroporto", "NOTAM", "Duplicados (outra língua)", "Severidade", "Categorias", "Pista",
            "Compr. original (m)", "Compr. disponível (m)", "Redução (m)", "Redução (%)", "Distâncias declaradas",
            "Vigência (B→C)", "Período (D)", "D interpretado?", "Texto (E)", "Observação", "Detecção", "Prob. ML",
            "Confiabilidade", "Confiab. (pontos)", "Motivos da confiabilidade", *CLOSURE_HEAD]
    rows = []
    for ap in sorted(res.runway_notams):
        for rn in sorted(res.runway_notams[ap],
                         key=lambda x: (SEVERITY_ORDER[x.severity], x.notam.start or res.window[0])):
            conf = notam_confidence(rn)
            rows.append([ap, rn.notam.number, ", ".join(rn.duplicates), rn.severity,
                         ", ".join(DESCRIPTION.get(c, c) for c in rn.cls.categories), ", ".join(rn.cls.runways),
                         rn.base_runway.length if rn.base_runway else None, rn.cls.available_length,
                         rn.reduction_m, rn.reduction_pct,
                         ", ".join(f"{k} {v}" for k, v in rn.cls.declared.items()), fmt_validity(rn.notam),
                         rn.notam.d, "sim" if rn.d_parsed else "NÃO", rn.notam.e, rn.note,
                         rn.cls.source, rn.cls.prob, conf.level, conf.score, conf.explanation,
                         *closure_cells(rn)])
    write_table(ws, head, rows, widths={5: 30, 12: 34, 13: 30, 15: 90, 16: 40, 21: 70}, sev_cols=(4,),
                 wrap_cols=(5, 13, 15, 16, 21))
