"""Excel export of the K-means clusters (patterns, suggested action and every NOTAM's cluster)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

if TYPE_CHECKING:  # type hints only: reporting does not depend on ml at runtime
    from ..ml.clustering import ClusterResult
    from ..ml.labeling import LabelSet


def write_clusters_excel(cr: ClusterResult, ls: LabelSet, path: str | Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Clusters"
    ws.append(["Cluster", "Tamanho", "Termos característicos", "Rótulos conhecidos", "Sem rótulo", "Pureza",
               "Majoritário", "Ação sugerida", "Exemplos"])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="1F3864")
    for cl in cr.clusters:
        ws.append([cl.id, cl.size, ", ".join(cl.terms), json.dumps(cl.label_counts, ensure_ascii=False),
                   cl.unlabeled, cl.purity, cl.majority, cl.action, "\n".join(cl.examples)])
    for col, w in zip("ABCDEFGHI", [8, 9, 40, 40, 10, 8, 22, 36, 90]):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws2 = wb.create_sheet("NOTAMs")
    ws2.append(["uid", "cluster", "rótulo", "fonte", "texto"])
    for i, uid in enumerate(ls.uids):
        ws2.append([uid, int(cr.assignment[i]), ls.labels[i] or "", ls.sources[i], ls.texts[i]])
    ws2.column_dimensions["E"].width = 100
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
