"""AISWEB XML parsing (fixtures and real responses)."""
import pytest
from notam_schedule.detection.rules import classify
from notam_schedule.ingestion.aisweb import AiswebClient
from notam_schedule.ingestion.aisweb import parse_notam_xml
from notam_schedule.ingestion.aisweb import AiswebError
from notam_schedule.domain.qcode import extract_qcode
from tests.helpers import FIX, REAL


def test_xml_parse_and_api_error():
    notams = parse_notam_xml((FIX / "SBKP.xml").read_text())
    assert len(notams) == 5 and notams[0].number == "C3001/26" and notams[0].d == "DLY 0300-0800"
    with pytest.raises(AiswebError):
        parse_notam_xml("Erro nos parametros obrigatórios")

def test_real_aisweb_xml_has_qcode_and_parses():
    ns = parse_notam_xml((REAL / "SBKP.xml").read_bytes())
    assert len(ns) == 7
    closed = next(n for n in ns if n.number == "F5927/26")
    assert extract_qcode(closed.raw) == "FALC"
    assert classify(closed).primary == "AERODROME_CLOSED"
    assert AiswebClient(None, offline_dir=REAL).runways("SBKP")[0].length == 3240
