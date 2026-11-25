"""FAA NMS-API adapter: GeoJSON parser, token flow, routing Brazil -> AISWEB / abroad -> FAA (no network)."""
import json

import pytest

from notam_schedule.config import FaaConfig
from notam_schedule.domain.ports import SourceError, covers
from notam_schedule.ingestion.aisweb import AiswebClient, AiswebError
from notam_schedule.ingestion.faa import FaaError, FaaNmsClient, parse_nms_geojson
from notam_schedule.ingestion.routing import RoutedSource
from notam_schedule.ingestion.sources import build_source
from tests.helpers import FIX, U

FAA = FIX / "faa"


def test_parse_international_notam_from_geojson():
    by = {n.number: n for n in parse_nms_geojson((FAA / "faa_SCEL.json").read_text())}
    assert "A4500/26" not in by                                  # test NOTAMs are dropped
    rwy = by["A4321/26"]
    assert (rwy.loc, rwy.ntype, rwy.ref, rwy.e) == ("SCEL", "R", "A4000/26", "RWY 17L/35R CLSD")
    assert (rwy.start, rwy.end, rwy.d) == (U(2026, 11, 15), U(2026, 12, 15, 23, 59), "DAILY 0300-0700")
    assert rwy.raw["q"] == "QMRLC" and rwy.international and rwy.id == "FAA-1000000000000001"
    assert by["A4400/26"].end_estimated


def test_parse_domestic_us_notam_and_perm():
    by = {n.number: n for n in parse_nms_geojson((FAA / "faa_KMCO.json").read_text())}
    assert by["11/045"].loc == "KMCO" and by["11/045"].e == "RWY 18L/36R CLSD"
    assert by["11/050"].permanent and by["11/050"].end is None


def test_parse_errors():
    with pytest.raises(FaaError):
        parse_nms_geojson("<html>")
    with pytest.raises(FaaError):
        parse_nms_geojson(json.dumps({"status": "Failure", "errors": [{"code": "1", "message": "bad"}]}))


class FakeResp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = body if isinstance(body, str) else json.dumps(body)

    def json(self):
        return json.loads(self.text)


class FakeSession:
    def __init__(self, gets):
        self.headers, self.gets, self.posts, self.calls = {}, list(gets), 0, []

    def post(self, url, data, auth, timeout):
        self.posts += 1
        assert data == {"grant_type": "client_credentials"} and auth == ("id", "segredo")
        return FakeResp(200, {"access_token": f"tok{self.posts}", "expires_in": "1799"})

    def get(self, url, params, headers, timeout):
        self.calls.append((url, params, headers))
        return self.gets.pop(0)


def test_client_token_renewal_and_cache(tmp_path):
    body = (FAA / "faa_SCEL.json").read_text()
    client = FaaNmsClient(FaaConfig("id", "segredo", "https://nms.test/nmsapi", "https://nms.test/v1/auth/token"),
                          cache_dir=tmp_path, retries=2)
    client.session = FakeSession([FakeResp(401, {"message": "Unauthorized"}), FakeResp(200, body)])
    assert {n.number for n in client.notams("SCEL")} >= {"A4321/26", "A4400/26"}
    url, params, headers = client.session.calls[-1]
    assert url == "https://nms.test/nmsapi/v1/notams" and params == {"location": "SCEL"}
    assert headers == {"Authorization": "Bearer tok2", "nmsResponseFormat": "GEOJSON"}   # renewed after the 401
    client.notams("SCEL")                                                             # served from cache
    assert len(client.session.calls) == 2 and client.session.posts == 2


def test_client_reports_http_errors(tmp_path):
    client = FaaNmsClient(FaaConfig("id", "segredo"), cache_dir=tmp_path, retries=1)
    client.session = FakeSession([FakeResp(403, {"message": "Forbidden"})])
    with pytest.raises(FaaError, match="HTTP 403"):
        client.notams("SCEL")
    with pytest.raises(SourceError):
        FaaNmsClient(None, cache_dir=tmp_path).notams("SCEL")


def test_routing_brazil_to_aisweb_and_abroad_to_faa():
    src = RoutedSource(AiswebClient(None, offline_dir=FIX), FaaNmsClient(None, offline_dir=FAA))
    assert src.name_for("SBKP") == "AISWEB" and src.name_for("SCEL") == "FAA"
    assert {n.number for n in src.notams("SCEL")} >= {"A4321/26"}
    assert src.notams("SBKP") and src.runways("SCEL") == []
    assert covers(src, "SCEL") and not covers(src, "LFPG")       # offline: only airports with a file
    assert not covers(RoutedSource(AiswebClient(None, offline_dir=FIX)), "SCEL")
    with pytest.raises(AiswebError):                              # never ask the AISWEB for a foreign airport
        AiswebClient(None, offline_dir=FIX).notams("SCEL")
    assert covers(build_source(None, offline_dir=FAA), "KMCO")


def test_live_shape_formatted_text_and_military_notices():
    by = {n.number: n for n in parse_nms_geojson((FAA / "faa_SCEL.json").read_text())}
    assert "W0657/26" not in by                                   # US military (DoD database) notice: skipped
    twy = by["A4400/26"]                                          # ICAO text in formattedText with CRLF
    assert "\r" not in twy.raw["icao_message"] and twy.raw["icao_message"].startswith("A4400/26 NOTAMN")


def test_same_notam_twice_in_the_nms_keeps_the_latest_update():
    body = json.loads((FAA / "faa_KMCO.json").read_text())
    twin = json.loads(json.dumps(body["data"]["geojson"][1]))
    twin["properties"]["coreNOTAMData"]["notam"].update(id="NMS_ID_2000000000000099", text="OBST CRANE 5 FT AGL",
                                                        lastUpdated="2026-11-11T12:00:00Z")
    body["data"]["geojson"].append(twin)
    by = [n for n in parse_nms_geojson(json.dumps(body)) if n.number == "11/050"]
    assert len(by) == 1 and by[0].e == "OBST CRANE 5 FT AGL"
