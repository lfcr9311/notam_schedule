"""FAA NOTAM Management Service (NMS-API) client: NOTAMs outside Brazil. Implements NotamSource.

Flow (nms-api 1.0.17): POST <auth_url> grant_type=client_credentials with HTTP Basic client_id:client_secret
-> bearer token valid ~30 min; GET <api_url>/v1/notams?location=<ICAO> with header nmsResponseFormat: GEOJSON
-> active NOTAMs of that location (international, domestic and military classifications).
The NMS does not publish runways, so runways() is empty (no ROTAER outside Brazil).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

from ...config import CACHE_DIR, FaaConfig
from ...domain.airport import Runway
from ...domain.notam import Notam
from ...domain.ports import RawArchive
from .errors import FaaError
from .parser import parse_nms_geojson, response_fingerprint

log = logging.getLogger(__name__)
TOKEN_MARGIN_S = 60


class FaaNmsClient:
    def __init__(
        self,
        config: FaaConfig | None,
        cache_dir: str | Path = CACHE_DIR,
        cache_ttl_min: int = 60,
        offline_dir: str | Path | None = None,
        timeout: int = 60,
        retries: int = 3,
        archive: RawArchive | None = None,
    ) -> None:
        self.config = config
        self.cache_dir = Path(cache_dir)
        self.cache_ttl = cache_ttl_min * 60
        self.offline_dir = Path(offline_dir) if offline_dir else None
        self.timeout = timeout
        self.retries = retries
        self.archive = archive
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "notam-schedule/0.1"
        self._token: tuple[str, float] | None = None   # (value, expires at epoch seconds)

    @property
    def enabled(self) -> bool:
        return self.config is not None or self.offline_dir is not None

    def _offline_file(self, icao: str) -> Path | None:
        assert self.offline_dir is not None
        d = self.offline_dir
        names = (d / f"faa_{icao}.json", d / f"{icao}.json", d / "faa" / f"faa_{icao}.json")
        return next((p for p in names if p.is_file()), None)

    def covers(self, icao: str) -> bool:
        """Online: every location. Offline: only the airports with a saved response."""
        if self.offline_dir is not None:
            return self._offline_file(icao) is not None
        return self.config is not None

    # ---------------------------------------------------------------- auth
    def _bearer(self, force: bool = False) -> str:
        if not force and self._token and self._token[1] > time.time() + TOKEN_MARGIN_S:
            return self._token[0]
        assert self.config is not None
        try:
            resp = self.session.post(self.config.auth_url, data={"grant_type": "client_credentials"},
                                     auth=(self.config.client_id, self.config.client_secret), timeout=30)
        except requests.RequestException as exc:
            raise FaaError(f"Autenticação na FAA falhou: {exc}") from exc
        if resp.status_code != 200:
            raise FaaError(f"Autenticação na FAA respondeu HTTP {resp.status_code} (verifique FAA_NMS_CLIENT_ID/"
                           "FAA_NMS_CLIENT_SECRET e se a URL é do mesmo ambiente das credenciais).")
        try:
            body = resp.json()
            token = body["access_token"]
        except (ValueError, KeyError) as exc:
            raise FaaError("Autenticação na FAA sem access_token na resposta.") from exc
        try:
            ttl = float(body.get("expires_in") or 1799)
        except (TypeError, ValueError):
            ttl = 1799.0
        self._token = (token, time.time() + ttl)
        return token

    # ---------------------------------------------------------------- HTTP / cache
    def _get_notams(self, icao: str) -> str:
        if self.offline_dir is not None:
            p = self._offline_file(icao)
            if p is not None:
                return p.read_text(encoding="utf-8")
            raise FaaError(f"Arquivo offline da FAA não encontrado para {icao} em {self.offline_dir}")

        cache_file = self.cache_dir / f"faa_notam_{icao}.json"
        if cache_file.is_file() and time.time() - cache_file.stat().st_mtime < self.cache_ttl:
            return cache_file.read_text(encoding="utf-8")
        if self.config is None:
            raise FaaError("FAA não configurada (defina FAA_NMS_CLIENT_ID e FAA_NMS_CLIENT_SECRET).")

        url = self.config.api_url.rstrip("/") + "/v1/notams"
        last: str = ""
        renewed = False
        for attempt in range(1, self.retries + 1):
            headers = {"Authorization": f"Bearer {self._bearer()}", "nmsResponseFormat": "GEOJSON"}
            try:
                resp = self.session.get(url, params={"location": icao}, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:
                last = str(exc)
                log.warning("FAA request failed for %s (attempt %d): %s", icao, attempt, exc)
                time.sleep(2 * attempt)
                continue
            if resp.status_code == 401 and not renewed:       # token revoked/expired early: renew once
                self._bearer(force=True)
                renewed = True
                continue
            if resp.status_code in (408, 429, 500, 502, 503, 504):
                last = f"HTTP {resp.status_code}"
                time.sleep(2 * attempt)
                continue
            if resp.status_code != 200:
                raise FaaError(f"FAA respondeu HTTP {resp.status_code} para {icao}: {' '.join(resp.text.split())[:200]}")
            text = resp.text
            parse_nms_geojson(text)                              # validate before caching
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(text, encoding="utf-8")
            if self.archive is not None:
                self.archive.store("faa", "notam", icao, text, response_fingerprint(text), datetime.now(timezone.utc))
            return text
        raise FaaError(f"Não foi possível consultar a FAA para {icao}: {last or 'sem resposta'}")

    # ---------------------------------------------------------------- NotamSource
    def notams(self, icao: str) -> list[Notam]:
        notams = parse_nms_geojson(self._get_notams(icao))
        return [n for n in notams if not n.loc or n.loc == icao]

    def runways(self, icao: str) -> list[Runway]:
        return []
