"""Builds the impact workbook: one sheet per module in sheets/."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from ...domain.changes import ChangeSet
from ...domain.impact import AnalysisResult
from .context import ReportContext
from .sheets import SHEETS


def write_excel(res: AnalysisResult, path: str | Path, params: dict, changes: ChangeSet | None = None) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ctx = ReportContext.build(res, params, changes)
    wb = Workbook()
    for sheet in SHEETS:
        sheet.add_sheet(wb, ctx)
    wb.save(path)
    return path
