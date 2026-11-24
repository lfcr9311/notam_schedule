"""Sheet "Sumário Executivo": Executive summary."""
from __future__ import annotations

from collections import Counter

from openpyxl.styles import Alignment

from ....domain.impact import CRITICAL, SEVERITY_ORDER
from ...formatting import SEVERITIES, SEVERITY_PT, WEEKDAYS_PT, flight_key, fmt_pattern, describe, worst_severity, zone_of
from ....domain.confidence import HIGH, LOW, MEDIUM
from ..context import ReportContext
from ..styles import FILL, SECTION_FONT, TITLE_FONT, style_header


def add_sheet(wb, ctx: ReportContext) -> None:
    res, params, aps, by_flight, by_rn, flight_worst, all_rns, rn_airport, dates, by_day_flights = ctx.res, ctx.params, ctx.aps, ctx.by_flight, ctx.by_rn, ctx.flight_worst, ctx.all_rns, ctx.rn_airport, ctx.dates, ctx.by_day_flights
    ws = wb.active
    ws.title = "Sumário Executivo"
    n_imp = len(by_flight)
    sev_count = Counter(flight_worst.values())
    ws.append(["Impacto de NOTAMs de pista na malha"])
    ws["A1"].font = TITLE_FONT
    ws.append([f"Período: {dates[0]:%d/%m/%Y} a {dates[-1]:%d/%m/%Y}" if dates else "", ])
    ws.append([])
    kpis = [
        ("Voos analisados", len(res.flights)),
        ("Voos impactados", n_imp),
        ("% da malha impactada", f"{100 * n_imp / len(res.flights):.1f}%" if res.flights else "0%"),
        *[(f"Voos com pior impacto {s} ({SEVERITY_PT[s]})", sev_count.get(s, 0)) for s in SEVERITIES],
        *[(f"Voos impactados com confiabilidade {lvl}", sum(c.level == lvl for c in ctx.flight_conf.values()))
          for lvl in (HIGH, MEDIUM, LOW)],
        ("Decolagens impactadas", len({flight_key(i) for i in res.impacts if i.side == "ORIGIN"})),
        ("Pousos impactados", len({flight_key(i) for i in res.impacts if i.side == "DESTINATION"})),
        ("Aeroportos com voos impactados", len({i.airport for i in res.impacts})),
        ("NOTAMs de pista vigentes no período", len(all_rns)),
        ("NOTAMs que afetam ao menos um voo", len(by_rn)),
        ("Aeroportos consultados", len(res.runway_notams)),
        ("Fora da cobertura DECEA", len(res.out_of_coverage)),
    ]
    ws.append(["Indicador", "Valor"])
    style_header(ws[ws.max_row])
    for k, v in kpis:
        ws.append([k, v])
        if "CRITICAL" in k and v:
            ws.cell(ws.max_row, 2).fill = FILL[CRITICAL]

    def section(title: str, head: list[str], rows: list[list], sev_col: int | None = None) -> None:
        ws.append([])
        ws.append([title])
        ws.cell(ws.max_row, 1).font = SECTION_FONT
        ws.append(head)
        style_header(ws[ws.max_row])
        for r in rows:
            ws.append(r)
            for c in ws[ws.max_row]:
                c.alignment = Alignment(wrap_text=True, vertical="top")
            if sev_col and r[sev_col - 1] in FILL:
                ws.cell(ws.max_row, sev_col).fill = FILL[r[sev_col - 1]]

    ap_rows = []
    for ap, cnt in Counter(i.airport for i in {(flight_key(i), i.side): i for i in res.impacts}.values()).most_common(10):
        sev = worst_severity(i.severity for i in res.impacts if i.airport == ap)
        days = {i.flight.date for i in res.impacts if i.airport == ap}
        ap_rows.append([ap, cnt, sev, len(days), "; ".join(sorted({i.rn.notam.number for i in res.impacts
                                                                     if i.airport == ap}))])
    section("Aeroportos com mais operações impactadas",
            ["Aeroporto", "Operações impactadas", "Pior severidade", "Dias com impacto", "NOTAMs"], ap_rows, 3)

    rn_rows = []
    for rn in sorted(all_rns, key=lambda r: (SEVERITY_ORDER[r.severity], -len(by_rn.get(id(r), [])))):
        items = by_rn.get(id(rn), [])
        if not items:
            continue
        rn_rows.append([rn_airport[id(rn)], rn.notam.number, rn.severity, describe(rn), len({flight_key(i) for i in items}),
                        fmt_pattern(rn.intervals, zone_of(aps, rn_airport[id(rn)]))])
    section("NOTAMs que afetam a malha (por severidade)",
            ["Aeroporto", "NOTAM", "Severidade", "O que é", "Voos afetados", "Quando (hora local)"], rn_rows[:25], 3)

    day_rows = []
    for d in sorted(by_day_flights, key=lambda d: (-sum(flight_worst[k] == CRITICAL for k in by_day_flights[d]),
                                                    -len(by_day_flights[d])))[:10]:
        ks = by_day_flights[d]
        day_rows.append([f"{d:%d/%m/%Y} ({WEEKDAYS_PT[d.weekday()]})", len(ks),
                         sum(flight_worst[k] == CRITICAL for k in ks),
                         ", ".join(sorted({i.airport for k in ks for i in by_flight[k]}))])
    section("Dias mais críticos", ["Data", "Voos impactados", "Voos críticos", "Aeroportos"], day_rows)

    ws.append([])
    ws.append(["Como ler: severidade CRITICAL = aeródromo fechado ou todas as pistas fechadas; HIGH = pista fechada "
               "com outra disponível ou redução ≥ 10%; MEDIUM = redução < 10% ou largura; LOW = superfície/obras. "
               f"Um voo é impactado quando o NOTAM está ativo entre {params.get('Margem (min) antes/depois', '60/60')} "
               "min antes/depois do STD (origem) ou STA (destino)."])
    ws.cell(ws.max_row, 1).alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=ws.max_row, start_column=1, end_row=ws.max_row, end_column=6)
    ws.row_dimensions[ws.max_row].height = 60
    for col, w in zip("ABCDEF", [38, 18, 18, 60, 14, 70]):
        ws.column_dimensions[col].width = w
