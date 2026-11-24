"""Weak label sources: Q-code and rules."""
from notam_schedule.domain.categories import AERODROME_CLOSED, LENGTH_REDUCTION, RUNWAY_CLOSED
from notam_schedule.ml import NOT_RUNWAY
from notam_schedule.ml.labeling import rule_label
from notam_schedule.domain.qcode import extract_qcode, label_from_qcode
from tests.helpers import mk


def test_qcode():
    assert extract_qcode({"q": "QMRLC"}) == "MRLC"
    assert extract_qcode({"cod": "C3001/26"}) is None
    assert label_from_qcode("MRLC") == RUNWAY_CLOSED
    assert label_from_qcode("MDCH") == LENGTH_REDUCTION
    assert label_from_qcode("FALC") == AERODROME_CLOSED
    assert label_from_qcode("LRAS") == NOT_RUNWAY      # runway lights: out of scope
    assert label_from_qcode("MXLC") == NOT_RUNWAY      # taxiway
    assert label_from_qcode("MRXX") is None            # inconclusive

def test_rule_label_weak_supervision():
    assert rule_label(mk(e="RWY 15/33 CLSD")) == RUNWAY_CLOSED
    assert rule_label(mk(e="ATIS U/S")) == NOT_RUNWAY                     # no runway mention
    assert rule_label(mk(e="PAPI RWY 15 U/S")) == NOT_RUNWAY              # equipment
    assert rule_label(mk(e="TWY C CLSD BTN TWY B AND RWY 15/33")) == NOT_RUNWAY
    assert rule_label(mk(e="PISTA 15/33 SEM OPERACAO")) is None           # uncertain -> unlabeled
