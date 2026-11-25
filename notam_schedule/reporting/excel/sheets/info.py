"""Sheet "Info": Run info."""
from __future__ import annotations

from datetime import datetime

from openpyxl.styles import Alignment, Font

from ...formatting import fmt_dt
from ..context import ReportContext


def add_sheet(wb, ctx: ReportContext) -> None:
    res, params = ctx.res, ctx.params
    ws = wb.create_sheet("Info")
    info = [
        ["Gerado em", datetime.now().strftime("%d/%m/%Y %H:%M")],
        ["Janela de análise (UTC)", f"{fmt_dt(res.window[0])} → {fmt_dt(res.window[1])}"],
        ["Voos analisados", len(res.flights)],
        ["Ocorrências de impacto (voo x NOTAM)", len(res.impacts)],
        ["Aeroportos consultados", ", ".join(sorted(res.runway_notams))],
    ]
    info += [[k, str(v)] for k, v in params.items()]
    if res.out_of_coverage:
        info.append(["Fora da cobertura (sem fonte de NOTAM)", ", ".join(res.out_of_coverage)])
    for ap, err in sorted(res.errors.items()):
        info.append([f"ERRO na consulta {ap}", err])
    info.append(["Abas", "Sumário Executivo → visão geral; Resumo por Dia/Aeroporto/NOTAM → consolidados; "
                         "Voos Impactados → 1 linha por voo; Janelas Ativas → cada período de vigência com os voos "
                         "afetados; Impacto por Voo → 1 linha por voo x NOTAM (detalhe completo); NOTAMs de Pista → "
                         "texto e interpretação; Revisar - Descartados → NOTAMs que citam pista mas foram excluídos."])
    info.append(["Observação", "Horários 'local' usam o fuso do aeroporto; NOTAMs são emitidos em UTC. A classificação "
                               "é automática por texto (item E): valide os NOTAMs críticos na fonte oficial."])
    for r in info:
        ws.append(r)
    ws.column_dimensions["A"].width = 42
    ws.column_dimensions["B"].width = 110
    for row in ws.iter_rows():
        row[0].font = Font(bold=True)
        row[1].alignment = Alignment(wrap_text=True, vertical="top")

