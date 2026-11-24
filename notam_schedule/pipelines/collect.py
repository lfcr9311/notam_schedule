"""Use case: grow the NOTAM corpus from the AISWEB API or from saved XML files."""
from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from ..domain.airport import BR_PREFIXES
from ..domain.ports import NotamSource, SourceError
from ..ingestion.aisweb import parse_notam_xml
from ..storage.corpus import Corpus
from ..storage.raw_archive import read_archived

log = logging.getLogger(__name__)


def collect(corpus: Corpus, source: NotamSource, icaos: Iterable[str]) -> tuple[dict[str, int], dict[str, str]]:
    """Download the NOTAMs of each Brazilian airport into the corpus. Returns (new per ICAO, errors per ICAO)."""
    added: dict[str, int] = {}
    errors: dict[str, str] = {}
    for icao in icaos:
        if not icao.startswith(BR_PREFIXES):
            continue
        try:
            added[icao] = corpus.add(source.notams(icao), f"api:{icao}")
        except SourceError as exc:
            log.error("%s: %s", icao, exc)
            errors[icao] = str(exc)
    return added, errors


def import_xml_dir(corpus: Corpus, folder: str | Path) -> int:
    """Import saved AISWEB NOTAM responses: *.xml, or the raw archive's *.xml.gz (searched recursively).

    ROTAER responses are skipped. Returns the number of new NOTAMs.
    """
    total = 0
    root = Path(folder)
    for p in sorted(root.rglob("*")):
        if not p.name.endswith((".xml", ".xml.gz")) or "rotaer" in p.relative_to(root).as_posix():
            continue
        total += corpus.add(parse_notam_xml(read_archived(p)), f"file:{p.relative_to(root).as_posix()}")
    return total
