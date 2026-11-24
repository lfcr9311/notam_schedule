"""Sheet "Revisar - Descartados": Discarded, for review."""
from __future__ import annotations

from ...formatting import fmt_validity
from ..context import ReportContext
from ..styles import write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    res = ctx.res
    ws = wb.create_sheet("Revisar - Descartados")
    head = ["Aeroporto", "NOTAM", "Vigência (B→C)", "Período (D)", "Texto (E)"]
    rows = [[ap, n.number, fmt_validity(n), n.d, n.e] for ap in sorted(res.discarded) for n in res.discarded[ap]]
    write_table(ws, head, rows, widths={3: 34, 4: 30, 5: 100}, wrap_cols=(4, 5))
