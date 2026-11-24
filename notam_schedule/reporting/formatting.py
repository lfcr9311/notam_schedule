"""Text formatting shared by the Excel and CSV reports (Portuguese labels for the operations team)."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..domain.airport import Airport
from ..domain.categories import (
    AERODROME_CLOSED, DESCRIPTION, LENGTH_REDUCTION, RUNWAY_CLOSED, SURFACE_CONDITION, WIDTH_REDUCTION,
)
from ..domain.confidence import impact_confidence
from ..domain.impact import CRITICAL, HIGH, LOW, MEDIUM, SEVERITY_ORDER, AnalysisResult, Impact, RunwayNotam

SEVERITIES = [CRITICAL, HIGH, MEDIUM, LOW]
SHORT = {
    AERODROME_CLOSED: "AD FECH",
    RUNWAY_CLOSED: "RWY FECH",
    LENGTH_REDUCTION: "COMPR",
    WIDTH_REDUCTION: "LARG",
    SURFACE_CONDITION: "SUPERF",
}
SEVERITY_PT = {CRITICAL: "crítico", HIGH: "alto", MEDIUM: "médio", LOW: "baixo"}
WEEKDAYS_PT = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]
SIDE_PT = {"ORIGIN": "ORIGEM", "DESTINATION": "DESTINO"}
EVENT_PT = {"ORIGIN": "Decolagem", "DESTINATION": "Pouso"}
MAX_LIST = 40  # max flights listed inside a single cell
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)



# ---------------------------------------------------------------------------------------- formatting
def fmt_dt(dt: datetime | None, tz=None) -> str:
    if dt is None:
        return ""
    return (dt.astimezone(tz) if tz else dt).strftime("%d/%m/%Y %H:%M")


def fmt_hm(dt: datetime | None, tz=None) -> str:
    return (dt.astimezone(tz) if tz else dt).strftime("%H:%M") if dt else ""


def fmt_windows(ws: list[tuple[datetime, datetime]], tz=None) -> str:
    out = []
    for s, e in ws:
        s2, e2 = (s.astimezone(tz), e.astimezone(tz)) if tz else (s, e)
        end = e2.strftime("%H:%M") if e2.date() == s2.date() or e - s < timedelta(hours=24) else fmt_dt(e2)
        out.append(f"{fmt_dt(s2)}–{end}")
    return " | ".join(out)


def fmt_duration(s: datetime, e: datetime) -> str:
    m = int((e - s).total_seconds() // 60)
    return f"{m // 1440}d {m % 1440 // 60:02d}h{m % 60:02d}" if m >= 1440 else f"{m // 60}h{m % 60:02d}"


def fmt_validity(n) -> str:
    end = "PERM" if n.permanent else fmt_dt(n.end) + (" EST" if n.end_estimated else "")
    return f"{fmt_dt(n.start)} → {end}"


def fmt_date_list(days: list[date]) -> str:
    """[1,2,3,5 Dec] -> '01–03/12, 05/12'."""
    days = sorted(set(days))
    out, i = [], 0
    while i < len(days):
        j = i
        while j + 1 < len(days) and days[j + 1] - days[j] == timedelta(days=1):
            j += 1
        a, b = days[i], days[j]
        out.append(a.strftime("%d/%m") if a == b else f"{a:%d}–{b:%d/%m}" if a.month == b.month
                   else f"{a:%d/%m}–{b:%d/%m}")
        i = j + 1
    return ", ".join(out)


def fmt_pattern(intervals: list[tuple[datetime, datetime]], tz: ZoneInfo) -> str:
    """Human-readable activity pattern in local time: '00:30–04:30 local: 02–05/12, 10/12'."""
    if not intervals:
        return ""
    groups: dict[tuple[str, str], list[date]] = defaultdict(list)
    long: list[str] = []
    for s, e in intervals:
        s2, e2 = s.astimezone(tz), e.astimezone(tz)
        if e - s >= timedelta(hours=24):
            long.append(f"contínuo {s2:%d/%m %H:%M} → {e2:%d/%m %H:%M}")
        else:
            groups[(s2.strftime("%H:%M"), e2.strftime("%H:%M"))].append(s2.date())
    parts = long + [f"{a}–{b} local em {len(ds)} dia(s): {fmt_date_list(ds)}"
                    for (a, b), ds in sorted(groups.items(), key=lambda kv: min(kv[1]))]
    return "; ".join(parts)


OPS_PT = {"": "", "LDG": " para pouso", "TKOF": " para decolagem"}
CLOSED_OPS_PT = {"": "pouso e decolagem", "LDG": "só pouso", "TKOF": "só decolagem"}
CLOSURE_HEAD = ["Tipo de fechamento", "Operações fechadas", "Exceção"]


def closure_cells(rn: RunwayNotam) -> list[str]:
    """Closure type, closed operations and exception (empty for non-closure categories)."""
    c = rn.cls
    if c.primary not in (RUNWAY_CLOSED, AERODROME_CLOSED):
        return ["", "", ""]
    kind = "Condicional (coordenação prévia/PPR)" if c.conditional else "Total"
    return [kind, CLOSED_OPS_PT[c.closed_ops], c.exception]


def availability_cells(i: Impact) -> list[str]:
    """Runways open during the flight window and whether the airport is left without a runway."""
    if i.open_runways is None:
        return ["(ROTAER indisponível)", ""]
    return [", ".join(i.open_runways) or "nenhuma", "SIM" if i.no_runway else "não"]


def describe(rn: RunwayNotam) -> str:
    """Plain-language description of what the NOTAM does."""
    c, rw = rn.cls, ", ".join(rn.cls.runways)
    cat = c.primary
    if cat == AERODROME_CLOSED:
        txt = "Aeródromo fechado"
    elif cat == RUNWAY_CLOSED:
        txt = (f"Pistas {rw} fechadas" if len(c.runways) > 1 else f"Pista {rw} fechada") if rw else "Pista fechada"
        txt += OPS_PT[c.closed_ops]
        if rn.closes_all_runways:
            txt += " (todas as pistas do aeroporto)"
        elif rn.airport_runway_count and rn.airport_runway_count > 1:
            txt += " (o aeroporto tem outra pista)"
        if c.exception:
            txt += f", exceto {c.exception}"
        if c.conditional:
            txt += " — operação possível com coordenação prévia (PPR)"
    elif cat == LENGTH_REDUCTION:
        txt = f"Redução de comprimento na pista {rw}" if rw else "Redução de comprimento"
        bits = []
        if rn.base_runway and rn.base_runway.length:
            bits.append(f"publicado {rn.base_runway.length} m")
        if c.available_length:
            bits.append(f"disponível {c.available_length} m")
        if rn.reduction_m is not None:
            bits.append(f"−{rn.reduction_m} m" + (f" ({rn.reduction_pct}%)" if rn.reduction_pct is not None else ""))
        if c.declared:
            bits.append("declaradas: " + ", ".join(f"{k} {v}" for k, v in c.declared.items()))
        if bits:
            txt += " — " + "; ".join(bits)
    elif cat == WIDTH_REDUCTION:
        txt = f"Largura reduzida na pista {rw}" if rw else "Largura reduzida"
    else:
        txt = f"Condição de superfície/obras na pista {rw}" if rw else "Condição de superfície/obras"
    return txt


def fmt_relation(ref: datetime | None, windows: list[tuple[datetime, datetime]]) -> str:
    """Where the flight's event falls relative to the NOTAM activity window."""
    if ref is None:
        return "voo sem horário (dia inteiro)"
    for s, e in windows:
        if s <= ref <= e:
            return "DURANTE a janela"
    s, e = min(windows, key=lambda w: min(abs((w[0] - ref).total_seconds()), abs((ref - w[1]).total_seconds())))
    if ref < s:
        return f"{int((s - ref).total_seconds() // 60)} min antes do início"
    return f"{int((ref - e).total_seconds() // 60)} min após o fim"


def airport_map(res: AnalysisResult) -> dict[str, Airport]:
    m: dict[str, Airport] = {}
    for f in res.flights:
        m.setdefault(f.origin.icao, f.origin)
        m.setdefault(f.destination.icao, f.destination)
    return m


def zone_of(aps: dict[str, Airport], icao: str) -> ZoneInfo:
    ap = aps.get(icao)
    return ap.zone if ap else ZoneInfo("America/Sao_Paulo")


def flight_key(i: Impact) -> tuple:
    return (i.flight.row, i.flight.flight, i.flight.date)


def worst_severity(sevs) -> str:
    return min(sevs, key=lambda s: SEVERITY_ORDER[s])


def airport_code(ap: Airport) -> str:
    return ap.iata or ap.icao


# ---------------------------------------------------------------------------------------- rows
IMPACT_HEAD = [
    "Data", "Dia sem.", "Voo", "Origem", "Destino", "Equip.", "STD (UTC)", "STD (local)", "STA (UTC)",
    "STA (local)", "Lado", "Aeroporto", "Evento", "Hora do evento (local)", "Severidade", "Categoria", "Pista",
    "Compr. original (m)", "Compr. disponível (m)", "Redução (m)", "Redução (%)", "Distâncias declaradas",
    "NOTAM ativo (local)", "NOTAM ativo (UTC)", "Relação com a janela", "Descrição do impacto", "NOTAM",
    "Vigência (B→C) UTC", "Período (D)", "Texto (E)", "Observação", "Detecção", "Prob. ML",
    "Confiabilidade", "Confiab. (pontos)", "Motivos da confiabilidade",
    *CLOSURE_HEAD, "Pistas sem fechamento na janela", "Aeroporto sem pista disponível?",
]


def impact_rows(res: AnalysisResult) -> tuple[list[str], list[list]]:
    rows = []
    for i in res.impacts:
        f, rn = i.flight, i.rn
        ap = f.origin if i.side == "ORIGIN" else f.destination
        tz = ap.zone
        ev = EVENT_PT[i.side]
        when = f"{fmt_hm(i.reference_utc, tz)} local" if i.reference_utc else "(sem horário)"
        desc = (f"{ev} {'de' if i.side == 'ORIGIN' else 'em'} {airport_code(ap)} às {when} — {describe(rn)} "
                f"({fmt_windows(i.windows, tz)} local); {fmt_relation(i.reference_utc, i.windows)}.")
        conf = impact_confidence(i)
        rows.append([
            f.date.strftime("%d/%m/%Y"), WEEKDAYS_PT[f.date.weekday()], f.flight, airport_code(f.origin),
            airport_code(f.destination), f.aircraft,
            fmt_dt(f.std_utc), fmt_dt(f.std_utc, f.origin.zone), fmt_dt(f.sta_utc), fmt_dt(f.sta_utc, f.destination.zone),
            SIDE_PT[i.side], i.airport, ev, fmt_dt(i.reference_utc, tz) if i.reference_utc else "(dia todo)",
            i.severity, DESCRIPTION.get(rn.cls.primary, rn.cls.primary), ", ".join(rn.cls.runways),
            rn.base_runway.length if rn.base_runway else None, rn.cls.available_length,
            rn.reduction_m, rn.reduction_pct,
            ", ".join(f"{k} {v}" for k, v in rn.cls.declared.items()),
            fmt_windows(i.windows, tz), fmt_windows(i.windows), fmt_relation(i.reference_utc, i.windows), desc,
            rn.notam.number, fmt_validity(rn.notam), rn.notam.d, rn.notam.e, rn.note, rn.cls.source, rn.cls.prob,
            conf.level, conf.score, conf.explanation, *closure_cells(rn), *availability_cells(i),
        ])
    return IMPACT_HEAD, rows


def fmt_flight_list(items: list[Impact], tz_of) -> str:
    items = sorted(items, key=lambda i: (i.reference_utc is None, i.reference_utc or EPOCH, i.flight.flight))
    txt = [f"{i.flight.date:%d/%m} {i.flight.flight} {'DEP' if i.side == 'ORIGIN' else 'ARR'} "
           f"{fmt_hm(i.reference_utc, tz_of(i)) if i.reference_utc else '--:--'}" for i in items[:MAX_LIST]]
    if len(items) > MAX_LIST:
        txt.append(f"... +{len(items) - MAX_LIST}")
    return "; ".join(txt)
