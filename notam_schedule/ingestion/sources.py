"""Builds the NOTAM source from the environment: AISWEB for Brazil + FAA NMS-API for the rest (when configured)."""
from __future__ import annotations

from pathlib import Path

from ..config import CACHE_DIR, AiswebConfig, FaaConfig
from ..domain.ports import RawArchive
from .aisweb import AiswebClient
from .faa import FaaNmsClient
from .routing import RoutedSource


def build_source(aisweb: AiswebConfig | None, faa: FaaConfig | None = None, offline_dir: str | Path | None = None,
                 cache_dir: str | Path = CACHE_DIR, cache_ttl_min: int = 60,
                 archive: RawArchive | None = None) -> RoutedSource:
    """Offline (offline_dir): <ICAO>.xml for Brazil and faa_<ICAO>.json for the other airports, in the same folder.
    Online: the FAA is used only when `faa` is given; otherwise airports outside Brazil are out of coverage."""
    if offline_dir:
        return RoutedSource(AiswebClient(None, offline_dir=offline_dir), FaaNmsClient(None, offline_dir=offline_dir))
    brazil = AiswebClient(aisweb, cache_dir=cache_dir, cache_ttl_min=cache_ttl_min, archive=archive)
    intl = FaaNmsClient(faa, cache_dir=cache_dir, cache_ttl_min=cache_ttl_min, archive=archive) if faa else None
    return RoutedSource(brazil, intl)
