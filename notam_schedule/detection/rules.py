"""Rule-based filter and classification of NOTAMs affecting runway condition.

Works on the item E text (Portuguese or English). Each NOTAM gets zero or more categories;
only those with at least one go on to the impact analysis.
The regex vocabulary (RWY, PISTA, FECHADA, ...) is NOTAM data and is intentionally bilingual.
"""
from __future__ import annotations

import re
from ..domain.categories import (
    AERODROME_CLOSED, CATEGORIES, LENGTH_REDUCTION, RUNWAY_CLOSED, SURFACE_CONDITION, WIDTH_REDUCTION,
    Classification,
)
from ..domain.notam import Notam
from ..domain.text import normalize

# Reference to a runway (excludes "pista de táxi/rolamento" = taxiway)
RWY = r"(?:\bRWY\b|\bPISTA\b(?!\s+DE\s+(?:TAXI|ROLAMENTO|ROLAGEM|TAXIAMENTO))|\bPST\b)"
NOT_RWY_OBJECTS = re.compile(r"\b(TWY|TAXI|TAXIWAY|PATIO|APN|APRON|STAND|BOX|POSICAO|ACFT STAND|PISTA DE TAXI|PISTA DE ROLAMENTO)\b")
# Equipment/procedure tied to a runway (the NOTAM is about it, not about the runway)
RE_EQUIP_RWY = re.compile(
    r"\b(PAPI|VASIS|ALS|ALSF|ILS|LOC|GP|DME|VOR|NDB|REIL|LGT|RNAV|RNP|GNSS|PROC|IAC|SID|STAR|APCH|"
    r"ANEMOMETRO|BALIZAMENTO|LUZES?)\b[^.]{0,15}?" + RWY
)
EQUIP_BETWEEN = re.compile(r"\b(PROC|RNAV|RNP|ILS|LOC|GP|VOR|DME|NDB|PAPI|ALS|LGT|SID|STAR|IAC|APCH|LUZES?)\b")
CLOSED = r"(?:CLSD|CLOSED|FECHAD[AO]S?|INTERDITAD[AO]S?|INTERDICAO|NOT AVBL|INDISPONIVE(?:L|IS)|FECHAMENTO)"
METERS = r"(\d{3,4})\s?(?:M|MTS|METROS|MTR)\b"

RE_AD_CLOSED = re.compile(r"\b(?:AD|AERODROMO|AEROPORTO|AIRPORT)\s+" + CLOSED)
RE_RWY_CLOSED = re.compile(RWY + r"([^.]{0,50}?)\b" + CLOSED)
RE_CLOSED_RWY = re.compile(r"\b(?:FECHAMENTO|INTERDICAO|CLOSURE)\s+(?:DA\s+|DE\s+|OF\s+)?" + RWY)
RE_PARTIAL = re.compile(
    r"\b(BTN|ENTRE|PRIMEIROS|ULTIMOS|FIRST|LAST|TRECHO|PARTE|PORCAO|PORTION|SECTION|SECAO|PARCIAL|PARTIAL)\b"
)

RE_LENGTH = [
    re.compile(r"\b(TORA|TODA|ASDA|LDA)\b"),
    re.compile(r"COMPRIMENTO[^.]{0,60}?(REDUZID|DISPONIVE|UTILIZAVE|UTIL\b|OPERACIONAL|LIMITAD)"),
    re.compile(r"(REDUZID[AO]S?|LIMITAD[AO]S?|REDUCED|LIMITED)[^.]{0,40}?(?:PARA|A|TO)\s*" + METERS),
    re.compile(r"\bLEN(?:GTH)?\b[^.]{0,40}?(REDUCED|AVBL|AVAILABLE|LIMITED)"),
    re.compile(r"DISTANCIAS?\s+DECLARADAS?|DECLARED\s+DIST"),
    re.compile(r"\b(THR|CABECEIRA)\b[^.]{0,30}?(DESLOCAD|DSPLCD|DISPLACED|RECUAD|TEMPORARI)"),
    re.compile(r"(DISPLACED|DESLOCAD[AO])\s+(THR|THRESHOLD|CABECEIRA)"),
    re.compile(METERS + r"[^.]{0,30}?(AVBL|AVAILABLE|DISPONIVE|UTILIZAVE)"),
    re.compile(r"(PRIMEIROS|ULTIMOS|FIRST|LAST)\s+" + METERS + r"[^.]{0,60}?" + CLOSED),
]
RE_WIDTH = re.compile(r"(LARGURA|WIDTH|WID)\b[^.]{0,40}?(REDUZID|REDUCED|DISPONIVE|AVBL|AVAILABLE|LIMITAD|LIMITED)")
RE_SURFACE = re.compile(
    RWY + r"[^.]{0,80}?\b(WIP|OBRAS?|SERVICOS?|MANUTENCAO|MAINT|EMBORRACHAMENTO|RUBBER|CONTAMINAD[AO]S?|"
    r"ESCORREGADI[AO]S?|SLIPPERY|FRICTION|ATRITO|ALAGAD[AO]S?|ALAGAMENTOS?|POCAS?|FLOODED|STANDING WATER|AGUA|IRREGULAR|BURACOS?|"
    r"HOLES?|DETERIORAD[AO]S?|DEGRADAD[AO]S?|FOD|GROOVING|RECAPEAMENTO|PAVIMENTO|PAVEMENT|SURFACE|SUPERFICIE|RWYCC|RCR)\b"
)
RE_SURFACE_REV = re.compile(
    r"\b(WIP|OBRAS?|SERVICOS? DE MANUTENCAO|EMBORRACHAMENTO|RUBBER|FOD|RECAPEAMENTO|GROOVING)\b"
    r"([^.]{0,30}?)" + RWY
)

RE_RWY_IDENT = re.compile(
    r"(?:\bRWY|\bPISTA|\bPST|\bTHR|\bCABECEIRA)\s*(\d{2}[LRC]?(?:\s*/\s*\d{2}[LRC]?)?)\b"
)
RE_DECLARED = re.compile(r"\b(TORA|TODA|ASDA|LDA)\b\s*(?:RWY\s*\d{2}[LRC]?\s*)?[:=]?\s*(\d{3,4})\s?(?:M|MTS|METROS)?\b")
RE_AVAILABLE = [
    re.compile(r"(?:REDUZID[AO]S?|LIMITAD[AO]S?|REDUCED|LIMITED)[^.]{0,40}?(?:PARA|A|TO)\s*" + METERS),
    re.compile(METERS + r"[^.]{0,30}?(?:AVBL|AVAILABLE|DISPONIVE|UTILIZAVE|UTEIS|USABLE)"),
    re.compile(r"COMPRIMENTO[^.]{0,40}?(?:DISPONIVEL|UTILIZAVEL|UTIL|OPERACIONAL)[^.]{0,20}?(?:DE\s+)?" + METERS),
]
RE_CLOSED_LEN = re.compile(r"(?:PRIMEIROS|ULTIMOS|FIRST|LAST)\s+" + METERS)
# "THR 04 DESLOCADA 289M" / "DISPLACED THR 15 300M"
RE_DISPLACED_LEN = re.compile(
    r"(?:\b(?:THR|CABECEIRA)\s*\d{2}[LRC]?\s+(?:DESLOCAD[AO]|DSPLCD|DISPLACED|RECUAD[AO])"
    r"|\b(?:DISPLACED|DESLOCAD[AO])\s+(?:THR|THRESHOLD|CABECEIRA)\s*\d{2}[LRC]?)"
    r"\s*(?:EM\s+|DE\s+|BY\s+)?(\d{2,4})\s?(?:M|MTS|METROS|MTR)\b"
)
# Declared-distance table: header "TORA TODA ASDA LDA" then "RWY 10: 2500M 2500M 2500M 2340M"
RE_DECLARED_HEADER = re.compile(r"\b(TORA|TODA|ASDA|LDA)\s+(TORA|TODA|ASDA|LDA)\s+(TORA|TODA|ASDA|LDA)\s+(TORA|TODA|ASDA|LDA)\b")
RE_DECLARED_ROW = re.compile(
    r"\b(?:RWY|PISTA|THR)?\s*(\d{2}[LRC]?)\s*:?\s+" + r"\s+".join([r"(\d{3,4})\s?M?"] * 4) + r"(?!\d)"
)
# Closure details. Vocabulary is NOTAM data (Portuguese and English).
RE_PPR = re.compile(r"\bPPR\b|MEDIANTE\s+(?:COOR|COORDENACAO|AUTH|AUTORIZACAO)|PODERAO\s+SER\s+AUT")
RE_LDG = re.compile(r"\b(LDG|POUSOS?|LANDINGS?|ATERRISS\w*)\b")
RE_TKOF = re.compile(r"\b(TKOF|T/O|DECOLAGE\w*|DECOLAGENS|TAKE-?OFFS?)\b")
RE_CLOSED_WORD = re.compile(CLOSED)
RE_REMARKS = re.compile(r"\b(RMK|OBS)\b")
RE_EXCEPT = re.compile(r"\b(?:EXC|EXCETO|EXCEPT)\b\s*(.+?)(?=\s*(?:\bDEVIDO\b|\bDUE\b|\bRMK\b|\bOBS\b|\.\s|\.$|$))")
RE_OPS_END = re.compile(r"\b(EXC|EXCETO|EXCEPT|DEVIDO|DUE)\b|[.,;]")
# Text after these words lists exceptions/remarks, not the affected runway
RE_RWY_SCOPE_END = re.compile(r"\b(EXC|EXCETO|EXCEPT|RMK|OBS)\b")
RE_RWY_CONTEXT = re.compile(RWY + r"|\bTHR\b|\bCABECEIRA\b|\b(TORA|TODA|ASDA|LDA)\b")


def _closure_is_runway(match: re.Match) -> bool:
    between = match.group(1) or ""
    return not NOT_RWY_OBJECTS.search(between) and not EQUIP_BETWEEN.search(between)


def classify(notam: Notam) -> Classification:
    text = normalize(notam.e)
    cats: set[str] = set()

    if RE_AD_CLOSED.search(text):
        cats.add(AERODROME_CLOSED)

    runway_closure = any(_closure_is_runway(m) for m in RE_RWY_CLOSED.finditer(text)) or bool(
        RE_CLOSED_RWY.search(text)
    )
    if runway_closure:
        cats.add(LENGTH_REDUCTION if RE_PARTIAL.search(text) else RUNWAY_CLOSED)

    has_rwy_context = RE_RWY_CONTEXT.search(text)
    if has_rwy_context and any(r.search(text) for r in RE_LENGTH):
        cats.add(LENGTH_REDUCTION)
    if has_rwy_context and RE_WIDTH.search(text):
        cats.add(WIDTH_REDUCTION)
    surface = RE_SURFACE.search(text) or any(
        not NOT_RWY_OBJECTS.search(m.group(2)) for m in RE_SURFACE_REV.finditer(text)
    )
    if surface and not cats and not RE_EQUIP_RWY.search(text):
        cats.add(SURFACE_CONDITION)

    c = Classification(categories=[k for k in CATEGORIES if k in cats])
    if c.relevant:
        extract_details(text, c)
    return c


def mentions_runway(text: str) -> bool:
    """Does the text mention a runway (RWY/PISTA/THR/declared distances)?"""
    return bool(RE_RWY_CONTEXT.search(normalize(text)))


def extract_details(text: str, c: Classification) -> Classification:
    """Fill runways, declared distances and lengths from the normalized text."""
    seen: list[str] = []
    cut = RE_RWY_SCOPE_END.search(text)
    scope = text[:cut.start()] if cut and RE_RWY_IDENT.search(text[:cut.start()]) else text
    for m in RE_RWY_IDENT.finditer(scope):
        ident = re.sub(r"\s+", "", m.group(1))
        if ident not in seen:
            seen.append(ident)
    # drop thresholds already contained in a full runway ("15" when "15/33" is present)
    full = [set(s.split("/")) for s in seen if "/" in s]
    c.runways = [s for s in seen if "/" in s or not any(s in f for f in full)]

    for name, val in RE_DECLARED.findall(text):
        v = int(val)
        if 100 <= v <= 6000:
            c.declared[name] = min(v, c.declared.get(name, v))
    header = RE_DECLARED_HEADER.search(text)
    if header:
        names = header.groups()
        for row in RE_DECLARED_ROW.finditer(text, header.end()):
            for name, val in zip(names, row.groups()[1:]):
                v = int(val)
                if 100 <= v <= 6000:
                    c.declared[name] = min(v, c.declared.get(name, v))

    candidates = [c.declared[k] for k in ("TORA", "LDA") if k in c.declared]
    if not candidates:
        for rx in RE_AVAILABLE:
            candidates += [int(x) for x in rx.findall(text) if 100 <= int(x) <= 6000]
    if candidates:
        c.available_length = min(candidates)
    m = RE_CLOSED_LEN.search(text) or RE_DISPLACED_LEN.search(text)
    if m:
        c.closed_length = int(m.group(1))
    _closure_details(text, c)
    return c


def _closure_details(text: str, c: Classification) -> None:
    """Which operations are closed, whether they can be authorised (PPR) and the EXC clause.

    Only the main clause (before RMK/OBS) defines the closure: remarks such as 'OPS LDG/TKOF PODERAO SER AUTH'
    mention operations but do not narrow the closure.
    """
    cut = RE_REMARKS.search(text)
    main = text[:cut.start()] if cut else text
    m = RE_CLOSED_WORD.search(main)
    if m:
        tail = main[m.end():m.end() + 50]
        end = RE_OPS_END.search(tail)
        window = main[max(0, m.start() - 25):m.end()] + (tail[:end.start()] if end else tail)
        ldg, tkof = bool(RE_LDG.search(window)), bool(RE_TKOF.search(window))
        c.closed_ops = "LDG" if ldg and not tkof else "TKOF" if tkof and not ldg else ""
    exc = RE_EXCEPT.search(main)
    if exc:
        c.exception = exc.group(1).strip(" ,;")[:80]
    c.conditional = bool(RE_PPR.search(text))
