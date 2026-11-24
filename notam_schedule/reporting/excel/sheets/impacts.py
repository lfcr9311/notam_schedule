"""Sheet "Impacto por Voo": Impact per flight x NOTAM."""
from __future__ import annotations

from ...formatting import impact_rows
from ..context import ReportContext
from ..styles import write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    res = ctx.res
    ws = wb.create_sheet("Impacto por Voo")
    head, rows = impact_rows(res)
    write_table(ws, head, rows, widths={16: 28, 23: 34, 24: 34, 25: 22, 26: 70, 28: 34, 29: 30, 30: 70, 31: 40, 36: 80},
                 sev_cols=(15,), wrap_cols=(23, 24, 26, 29, 30, 31, 36))
