"""Raw response archive (NOTAM history) and the AISWEB client that feeds it."""
from datetime import datetime, timezone

from notam_schedule.config import AiswebConfig
from notam_schedule.ingestion.aisweb import AiswebClient, response_fingerprint
from notam_schedule.pipelines.collect import import_xml_dir
from notam_schedule.storage.corpus import Corpus
from notam_schedule.storage.raw_archive import FileRawArchive, read_archived
from tests.helpers import FIX

T0 = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
XML = FIX.joinpath("SBKP.xml").read_text(encoding="utf-8")


def test_fingerprint_ignores_volatile_root_attributes():
    a = '<?xml version="1.0"?><aisweb><notam id="aaa" total="1" updatedat="2026-11-11 16:03:00"><item><e>X</e></item></notam></aisweb>'
    b = '<?xml version="1.0"?><aisweb>\n  <notam id="bbb" total="1" updatedat="2026-11-17 09:00:00">\n<item><e>X</e></item></notam></aisweb>'
    c = a.replace("<e>X</e>", "<e>Y</e>")
    assert response_fingerprint(a) == response_fingerprint(b)
    assert response_fingerprint(a) != response_fingerprint(c)


def test_archive_keeps_only_distinct_responses_and_never_overwrites(tmp_path):
    arc = FileRawArchive(tmp_path)
    assert arc.store("aisweb", "notam", "SBKP", XML, response_fingerprint(XML), T0)
    assert not arc.store("aisweb", "notam", "SBKP", XML, response_fingerprint(XML), T0.replace(hour=13))
    changed = XML.replace("</aisweb>", "<!-- new --></aisweb>")
    assert arc.store("aisweb", "notam", "SBKP", changed + " ", "other-fingerprint", T0)  # same second
    files = list(arc.files())
    assert [p.name for p in files] == ["20261117T120000Z.xml.gz", "20261117T120000Z_1.xml.gz"]
    assert read_archived(files[0]) == XML

    corpus = Corpus(tmp_path / "c.sqlite")
    assert import_xml_dir(corpus, tmp_path / "aisweb" / "notam") > 0      # corpus rebuilt from the archive


class _Resp:
    def __init__(self, text):
        self.text, self.encoding = text, "utf-8"

    def raise_for_status(self):
        pass


def test_client_archives_api_responses_but_not_cache_hits(tmp_path):
    arc = FileRawArchive(tmp_path / "raw")
    client = AiswebClient(AiswebConfig("k", "p"), cache_dir=tmp_path / "cache", cache_ttl_min=60, archive=arc)
    calls = []
    client.session.get = lambda *a, **k: calls.append(1) or _Resp(XML)
    assert client.notams("SBKP")
    assert client.notams("SBKP")                     # second read comes from the cache
    assert len(calls) == 1 and len(list(arc.files())) == 1
