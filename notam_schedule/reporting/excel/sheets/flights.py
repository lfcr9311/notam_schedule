"""Sheet "Voos Impactados": Impacted flights (one row per flight)."""
from __future__ import annotations

from ...formatting import EPOCH, EVENT_PT, SHORT, WEEKDAYS_PT, airport_code, fmt_dt, fmt_relation, describe, fmt_windows
from ..context import ReportContext
from ..styles import write_table


def add_sheet(wb, ctx: ReportContext) -> None:
    tz_of, by_flight, flight_worst = ctx.tz_of, ctx.by_flight, ctx.flight_worst
    ws = wb.create_sheet("Voos Impactados")
    head = ["Data", "Dia sem.", "Voo", "Origem", "Destino", "Equip.", "STD (local)", "STA (local)",
            "STD (UTC)", "STA (UTC)", "Pior severidade", "Nº NOTAMs", "Impacto na ORIGEM", "Impacto no DESTINO",
            "Resumo", "Confiabilidade (menor)", "Confiab. (pontos)", "Motivos da confiabilidade"]
    rows = []
    for k in sorted(by_flight, key=lambda k: (k[2], by_flight[k][0].flight.std_utc or EPOCH,
                                              k[1])):
        items = by_flight[k]
        f = items[0].flight

        def side_txt(side: str) -> str:
            return "\n".join(f"{i.airport} {i.rn.notam.number} [{i.severity}] {describe(i.rn)} "
                             f"({fmt_windows(i.windows, tz_of(i))} local; {fmt_relation(i.reference_utc, i.windows)})"
                             + (" — SEM PISTA DISPONÍVEL" if i.no_runway else
                                f" — pistas abertas: {', '.join(i.open_runways) or 'nenhuma'}"
                                if i.open_runways is not None and i.rn.cls.primary == "RUNWAY_CLOSED" else "")
                             for i in items if i.side == side)

        summary = "; ".join(sorted({f"{EVENT_PT[i.side]} {i.airport}: {SHORT.get(i.rn.cls.primary)}"
                                    for i in items}))
        rows.append([f.date.strftime("%d/%m/%Y"), WEEKDAYS_PT[f.date.weekday()], f.flight, airport_code(f.origin),
                     airport_code(f.destination), f.aircraft, fmt_dt(f.std_utc, f.origin.zone),
                     fmt_dt(f.sta_utc, f.destination.zone), fmt_dt(f.std_utc), fmt_dt(f.sta_utc), flight_worst[k],
                     len({id(i.rn) for i in items}), side_txt("ORIGIN"), side_txt("DESTINATION"), summary,
                     ctx.flight_conf[k].level, ctx.flight_conf[k].score, ctx.flight_conf[k].explanation])
    write_table(ws, head, rows, widths={13: 70, 14: 70, 15: 45, 18: 70}, sev_cols=(11,), wrap_cols=(13, 14, 15, 18))
