"""Excel styles and the generic table writer shared by the sheets."""
from __future__ import annotations

from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from ...domain.impact import CRITICAL, HIGH, LOW, MEDIUM

FILL = {
    CRITICAL: PatternFill("solid", fgColor="F8696B"),
    HIGH: PatternFill("solid", fgColor="FFB366"),
    MEDIUM: PatternFill("solid", fgColor="FFE699"),
    LOW: PatternFill("solid", fgColor="DDEBF7"),
}

HEADER_FILL = PatternFill("solid", fgColor="1F3864")

HEADER_FONT = Font(bold=True, color="FFFFFF")

TITLE_FONT = Font(bold=True, size=14, color="1F3864")

SECTION_FONT = Font(bold=True, size=12, color="1F3864")

def style_header(row) -> None:
    for c in row:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)

def write_table(ws, head: list[str], rows: list[list], widths: dict[int, int] | None = None,
                 sev_cols: tuple[int, ...] = (), wrap_cols: tuple[int, ...] = ()) -> None:
    ws.append(head)
    style_header(ws[1])
    for r in rows:
        ws.append(r)
    for idx, h in enumerate(head, start=1):
        if widths and idx in widths:
            w = widths[idx]
        elif rows:
            w = min(max(len(str(h)), *(len(str(r[idx - 1] or "")) for r in rows[:500]), 8) + 2, 45)
        else:
            w = len(h) + 4
        ws.column_dimensions[get_column_letter(idx)].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=c.column in wrap_cols, vertical="top")
        for sc in sev_cols:
            sev = row[sc - 1].value
            if sev in FILL:
                row[sc - 1].fill = FILL[sev]
    ws.freeze_panes = "A2"
    if rows:
        ws.auto_filter.ref = ws.dimensions
