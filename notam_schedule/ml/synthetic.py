"""SYNTHETIC NOTAM generator for pipeline demos and tests.

Do not use it to measure real performance: valid metrics come from real NOTAMs labeled by experts.
Includes wordings the rules do not cover, to show the model's gain. Template texts are NOTAM data.
"""
from __future__ import annotations

import random

from ..domain.categories import (
    AERODROME_CLOSED, LENGTH_REDUCTION, RUNWAY_CLOSED, SURFACE_CONDITION, WIDTH_REDUCTION,
)
from ..domain.notam import Notam
from . import NOT_RUNWAY

TEMPLATES = {
    RUNWAY_CLOSED: [
        "RWY {r} CLSD", "RWY {r} CLSD DUE {reason}", "PISTA {r} FECHADA PARA POUSOS E DECOLAGENS",
        "PISTA {r} INTERDITADA DEVIDO {reason_pt}", "RWY {r} NOT AVBL", "PISTA {r} INDISPONIVEL",
        "PISTA {r} FECHADA AO TRAFEGO", "RWY {r} CLOSED TO ALL ACFT",
        # wordings not covered by the rules
        "PISTA {r} SEM OPERACAO", "RWY {r} OUT OF SERVICE FOR ACFT OPS", "PISTA {r} NAO OPERACIONAL",
    ],
    LENGTH_REDUCTION: [
        "RWY {r} TORA {d}M TODA {d}M ASDA {d}M LDA {d2}M", "THR {t} DESLOCADA {s}M",
        "RWY {t} DISPLACED THR {s}M", "PISTA {r} COMPRIMENTO DISPONIVEL {d} METROS",
        "RWY {r} LENGTH REDUCED TO {d}M DUE WIP", "PRIMEIROS {s}M DA PISTA {t} INTERDITADOS",
        "DISTANCIAS DECLARADAS RWY {r} ALTERADAS: TORA {d}M LDA {d2}M", "RWY {r} CLSD BTN THR {t} AND TWY {twy}",
        # wordings not covered by the rules
        "PISTA {r} OPERANDO COM {d} METROS UTEIS", "RWY {r} ONLY {d}M USABLE", "PISTA {r} ENCURTADA EM {s}M",
        "CABECEIRA {t} RECUADA EM {s} METROS",
    ],
    WIDTH_REDUCTION: [
        "RWY {r} WIDTH REDUCED TO {w}M", "PISTA {r} LARGURA DISPONIVEL REDUZIDA PARA {w}M",
        "PISTA {r} COM LARGURA LIMITADA A {w}M", "RWY {r} WID AVBL {w}M",
        "PISTA {r} SOMENTE {w}M DE LARGURA UTILIZAVEIS",
    ],
    AERODROME_CLOSED: [
        "AD CLSD", "AD CLSD DUE {reason}", "AERODROMO FECHADO", "AEROPORTO FECHADO PARA OPERACOES",
        "AD CLSD EXC ACFT IN EMERG", "AERODROMO INTERDITADO",
    ],
    SURFACE_CONDITION: [
        "WIP ON RWY {r}", "PISTA {r} COM EMBORRACHAMENTO EXCESSIVO", "RWY {r} SLIPPERY WHEN WET",
        "OBRAS NA PISTA {r} PROX THR {t}", "PISTA {r} COM IRREGULARIDADES NO PAVIMENTO",
        "RWY {r} RUBBER DEPOSITS", "PISTA {r} ESCORREGADIA QUANDO MOLHADA",
        "PISTA {r} COM POCAS DE AGUA APOS CHUVA", "FOD NA PISTA {r}",
    ],
    NOT_RUNWAY: [
        "PAPI RWY {t} U/S", "PAPI RWY {t} INOPERANTE", "ILS RWY {t} U/S", "LOC RWY {t} INOPERANTE",
        "TWY {twy} CLSD", "PISTA DE TAXI {twy} FECHADA", "TWY {twy} CLSD BTN TWY {twy2} AND RWY {r}",
        "APN {n} CLSD", "PATIO {n} INTERDITADO", "GUINDASTE {h}FT AGL PROX CABECEIRA {t}",
        "CRANE {h}FT AMSL {dist}NM FM THR {t}", "BIRD CONCENTRATION IN THE VICINITY OF AD",
        "CONCENTRACAO DE AVES NAS PROXIMIDADES DO AD", "VOR {vor} U/S", "NDB {vor} INOPERANTE",
        "ALS RWY {t} U/S", "BALIZAMENTO LATERAL DA PISTA {r} INOPERANTE", "RWY {t} EDGE LGT U/S",
        "ATIS FREQ 127.{n}MHZ U/S", "TWR HR SER 0900-2100", "OBST LGT ON ANTENNA {h}FT INOPERANTE",
        "RNAV RWY {t} PROC NOT AVBL", "SID {vor}1A RWY {t} SUSPENSA", "DRONE ACTIVITY {dist}NM FM AD",
        "FAROL ROTATIVO DE AERODROMO INOPERANTE", "ANEMOMETRO RWY {t} INOPERANTE",
        "AD HR OPS 0900-2300", "ABAST COMBUSTIVEL JET A1 NOT AVBL", "SALA AIS FECHADA",
    ],
}
REASONS = ["WIP", "MAINT", "ACFT DISABLED ON RWY", "EMERGENCY", "MIL EXER"]
REASONS_PT = ["OBRAS", "MANUTENCAO", "ACFT ACIDENTADA", "SERVICOS NO PAVIMENTO"]


def _rwy(rng: random.Random) -> tuple[str, str]:
    a = rng.randint(1, 18)
    suf = rng.choice(["", "", "", "L", "R"])
    opp = {"L": "R", "R": "L", "": ""}[suf]
    t = f"{a:02d}{suf}"
    return f"{t}/{a + 18:02d}{opp}", t


def generate(n: int = 1000, seed: int = 7, runway_share: float = 0.25) -> list[tuple[Notam, str]]:
    """Return [(Notam, true label)]."""
    rng = random.Random(seed)
    cats = [RUNWAY_CLOSED, LENGTH_REDUCTION, WIDTH_REDUCTION, AERODROME_CLOSED, SURFACE_CONDITION]
    weights = [0.25, 0.35, 0.08, 0.12, 0.20]
    locs = ["SBKP", "SBGR", "SBSP", "SBRJ", "SBGL", "SBCF", "SBBR", "SBCT", "SBPA", "SBRF", "SBSV", "SBFZ"]
    out = []
    for i in range(n):
        label = rng.choices(cats, weights)[0] if rng.random() < runway_share else NOT_RUNWAY
        r, t = _rwy(rng)
        d = rng.randrange(1200, 3200, 10)
        txt = rng.choice(TEMPLATES[label]).format(
            r=r, t=t, d=d, d2=d - rng.randrange(0, 400, 10), s=rng.randrange(100, 900, 10),
            w=rng.choice([23, 30, 35, 40]), twy=rng.choice("ABCDEFGHJKLM"), twy2=rng.choice("ABCDEFGHJKLM"),
            n=rng.randint(1, 9), h=rng.randrange(100, 900, 10), dist=rng.randint(1, 9),
            vor=rng.choice(["KPS", "CPN", "BCO", "PCX", "UBT"]), reason=rng.choice(REASONS),
            reason_pt=rng.choice(REASONS_PT),
        )
        if rng.random() < 0.3:
            txt += rng.choice([". REF AIP AD 2", ". CONFORME COORDENACAO COM ATC", " DUE WIP", "."])
        loc = rng.choice(locs)
        out.append((Notam(f"S{i:05d}/26", loc, "N", "", None, None, False, False, "", txt,
                          {"id": str(i), "loc": loc, "e": txt}), label))
    return out
