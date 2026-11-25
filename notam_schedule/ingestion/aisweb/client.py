"""AISWEB (DECEA) API client: NOTAM and ROTAER, with disk cache and offline mode. Implements NotamSource."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ...config import CACHE_DIR, AiswebConfig
from ...domain.airport import BR_PREFIXES, Runway
from ...domain.notam import Notam
from ...domain.ports import RawArchive
from .parser import parse_notam_xml, parse_rotaer_xml, response_fingerprint
from .errors import AiswebError

log = logging.getLogger(__name__)


class AiswebClient:
    def __init__(
        self,
        config: AiswebConfig | None,
        cache_dir: str | Path = CACHE_DIR,
        cache_ttl_min: int = 60,
        offline_dir: str | Path | None = None,
        timeout: int = 30,
        retries: int = 3,
        archive: RawArchive | None = None,
    ) -> None:
        """archive: keeps every distinct API response (history). Offline and cached reads are not archived."""
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.cache_ttl = cache_ttl_min * 60
        self.offline_dir = Path(offline_dir) if offline_dir else None
        self.timeout = timeout
        self.retries = retries
        self.archive = archive
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "notam-schedule/0.1"

    # ---------------------------------------------------------------- HTTP / cache
    def _get(self, area: str, icao: str, **params: str) -> str:
        cache_file = self.cache_dir / f"{area}_{icao}.xml"
        if self.offline_dir is not None:
            names = (f"{icao}.xml", f"notam_{icao}.xml") if area == "notam" else (f"{area}_{icao}.xml",)
            for name in names:
                p = self.offline_dir / name
                if p.is_file():
                    return p.read_text(encoding="utf-8")
            raise AiswebError(f"Arquivo offline não encontrado para {icao} em {self.offline_dir}")

        if cache_file.is_file() and time.time() - cache_file.stat().st_mtime < self.cache_ttl:
            return cache_file.read_text(encoding="utf-8")

        if self.config is None:
            raise AiswebError("Sem credenciais da API e sem diretório offline.")
        query = {"apiKey": self.config.api_key, "apiPass": self.config.api_pass, "area": area, **params}
        last_exc: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                resp = self.session.get(self.config.base_url, params=query, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = resp.encoding or "utf-8"
                text = resp.text
                if not text.lstrip().startswith(("<?xml", "<aisweb")):
                    raise AiswebError(" ".join(text.split())[:300] or "resposta vazia")
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(text, encoding="utf-8")
                if self.archive is not None:
                    self.archive.store("aisweb", area, icao, text, response_fingerprint(text), datetime.now(timezone.utc))
                return text
            except AiswebError:
                raise
            except requests.RequestException as exc:
                last_exc = exc
                log.warning("Request failed for %s/%s (attempt %d): %s", area, icao, attempt, exc)
                time.sleep(2 * attempt)
        raise AiswebError(f"Não foi possível consultar a API para {icao}: {last_exc}")

    # ---------------------------------------------------------------- endpoints
    def covers(self, icao: str) -> bool:
        """AISWEB is used only for Brazilian airports; NOTAMs abroad come from the FAA (ingestion.faa)."""
        return icao.upper().startswith(BR_PREFIXES)

    def notams(self, icao: str) -> list[Notam]:
        if not self.covers(icao):
            raise AiswebError(f"{icao} não é brasileiro: NOTAMs internacionais vêm da FAA, não da AISWEB.")
        notams = parse_notam_xml(self._get("notam", icao, icaocode=icao))
        # The API may return NOTAMs for other locations (e.g. FIR); keep only the aerodrome's
        return [n for n in notams if not n.loc or n.loc == icao]

    def runways(self, icao: str) -> list[Runway]:
        if not self.covers(icao):
            return []
        try:
            text = self._get("rotaer", icao, icaoCode=icao)
        except AiswebError as exc:
            log.warning("ROTAER unavailable for %s: %s", icao, exc)
            return []
        return parse_rotaer_xml(text)
