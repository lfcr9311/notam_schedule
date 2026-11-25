"""Rule-based detection and extraction of runway restrictions."""
import pytest
from notam_schedule.detection.rules import classify, extract_details
from notam_schedule.domain.categories import (
    AERODROME_CLOSED, LENGTH_REDUCTION, RUNWAY_CLOSED, SURFACE_CONDITION, WIDTH_REDUCTION, Classification,
)
from tests.helpers import mk, real_notam


@pytest.mark.parametrize("text,cat", [
    ("RWY 15/33 CLSD", RUNWAY_CLOSED),
    ("PISTA 10/28 FECHADA PARA POUSOS E DECOLAGENS", RUNWAY_CLOSED),
    ("AD CLSD", AERODROME_CLOSED),
    ("RWY 15/33 CLSD BTN THR 15 AND TWY C", LENGTH_REDUCTION),
    ("RWY 09 TORA 1800M TODA 1800M ASDA 1800M LDA 1650M", LENGTH_REDUCTION),
    ("THR 02R DESLOCADA 300M", LENGTH_REDUCTION),
    ("RWY 33 DISPLACED THR 400M", LENGTH_REDUCTION),
    ("PRIMEIROS 500M DA PISTA 11 INTERDITADOS", LENGTH_REDUCTION),
    ("RWY 11/29 1500M AVBL", LENGTH_REDUCTION),
    ("RWY 02L/20R LARGURA DISPONIVEL REDUZIDA PARA 30M", WIDTH_REDUCTION),
    ("WIP ON RWY 15/33 EDGE", SURFACE_CONDITION),
    ("PISTA 17 COM EMBORRACHAMENTO EXCESSIVO", SURFACE_CONDITION),
])
def test_classify_relevant(text, cat):
    assert classify(mk(e=text)).primary == cat

@pytest.mark.parametrize("text", [
    "PAPI RWY 33 INOPERANTE",
    "TWY C CLSD BTN TWY B AND RWY 15/33",
    "PISTA DE TAXI A FECHADA",
    "ILS RWY 15 U/S",
    "APN 2 CLSD",
    "GUINDASTE 45M AGL PROX CABECEIRA 15",
    "RNAV RWY 06R PROC NOT AVBL",
    "PAPI RWY 04 INOPERANTE DUE WIP",
])
def test_classify_irrelevant(text):
    assert not classify(mk(e=text)).relevant

def test_extraction():
    c = classify(mk(e="RWY 15/33 COMPRIMENTO REDUZIDO. TORA RWY 15 2400M TODA 2400M ASDA 2400M LDA 2200M"))
    assert c.runways == ["15/33"]
    assert c.declared == {"TORA": 2400, "TODA": 2400, "ASDA": 2400, "LDA": 2200}
    assert c.available_length == 2200
    assert classify(mk(e="RWY 11/29 REDUZIDA PARA 1800 METROS")).available_length == 1800
    assert extract_details("PISTA 02R/20L OPERANDO COM 1100 METROS UTEIS", Classification()).available_length == 1100

def test_declared_distance_table_takes_lowest():
    c = classify(real_notam("DIST DECLARADAS RWY 04/22 MODIFICADAS PARA:\nTORA TODA ASDA LDA\n"
                    "RWY 04 1837 1837 1837 1548\nRWY 22 1638 1638 1927 1837\nREF: AIP AD 2.13"))
    assert c.declared["LDA"] == 1548 and c.available_length == 1548

def test_displaced_threshold_length():
    c = classify(real_notam("THR 04 DESLOCADA 289M\nREF: AIP AD 2.12"))
    assert c.primary == "LENGTH_REDUCTION" and c.closed_length == 289

def test_runway_after_exc_is_not_closed():
    c = classify(real_notam("RWY 02L/20R CLSD DEVIDO OBRAS, EXC TRECHOS ESSENCIAIS A OPS TAX DA RWY 02R/20L"))
    assert c.runways == ["02L/20R"]


def test_portuguese_word_endings_are_recognised():
    """Stems such as ESCORREGADI/ALAGAD must match ESCORREGADIA, ALAGADOS... (regression, SBGO F6662/26)."""
    for text in ("RWY 14 BTN 400M E 500M LADO ESQUERDO ESCORREGADIA QUANDO MOLHADA", "RWY 14 CONTAMINADA",
                 "RWY 14 ALAGADA", "PISTA 10 COM POCOS ALAGADOS", "RWY 14 COM ALAGAMENTO", "RWY 09 DEGRADADA"):
        assert classify(mk(e=text)).categories == [SURFACE_CONDITION], text


def test_restriction_words_with_endings_keep_runway_notams_out_of_not_runway():
    from notam_schedule.ml.labeling import rule_label
    # navaid NOTAM that also says the runway is reduced, missed by the main rules: must stay uncertain
    # (left to the model / expert), never weak-labelled NOT_RUNWAY just because REDUZIDA ends in -A
    text = "VOR RWY 15 U/S, RWY 15 REDUZIDA"
    assert classify(mk(e=text)).categories == []
    assert rule_label(mk(e=text)) is None


def test_closure_details_operations_ppr_and_exception():
    c = classify(mk(e="RWY 07/25 CLSD LDG E TKOF EXC INT RWY 03/21 DEVIDO EVENTO"))
    assert (c.closed_ops, c.conditional, c.exception) == ("", False, "INT RWY 03/21")
    assert c.runways == ["07/25"]                                # 03/21 is the exception, not closed
    c = classify(mk(e="RWY 10/28 CLSD DEVIDO OBRAS RMK: OPS LDG/TKOF PODERAO SER AUTH MEDIANTE COOR PREVIA"))
    assert c.conditional and c.closed_ops == ""                  # LDG/TKOF in the RMK does not narrow it
    assert classify(mk(e="RWY 15 CLSD FOR LDG")).closed_ops == "LDG"
    assert classify(mk(e="PISTA 15/33 FECHADA PARA DECOLAGENS")).closed_ops == "TKOF"
    assert classify(mk(e="PISTA 10/28 INTERDITADA PARA POUSOS E DECOLAGENS")).closed_ops == ""


def test_runway_only_bounding_a_taxiway_segment_is_not_a_runway_notam():
    assert classify(mk("TWY D BTN APN COMMERCIAL AND RWY 11/29 CLSD WIP MAINT")).categories == []
    assert classify(mk("TWY C ENTRE O PATIO E A RWY 11/29 FECHADA")).categories == []
    assert classify(mk("TWY A CLSD. RWY 11/29 CLSD")).categories == ["RUNWAY_CLOSED"]
    assert classify(mk("RWY 17L/35R CLSD BTN TWY B AND TWY C")).categories == ["LENGTH_REDUCTION"]
