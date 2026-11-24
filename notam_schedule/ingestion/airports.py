"""Airport table: IATA -> ICAO conversion and time zone.

Sources, in increasing priority:
  1. Built-in table (notam_schedule/resources/airports.csv: iata,icao,name,tz).
  2. User file(s) passed with --airports, or data/reference/airports.csv when present. Two layouts are accepted:
     - iata,icao,name,tz
     - OurAirports-style: ident,latitude_deg,longitude_deg,iata_code
     The user file wins for IATA -> ICAO. Time zone comes from the file, else from the built-in
     table, else from the coordinates (if the optional `timezonefinder` package is installed).
"""
from __future__ import annotations

import csv
import logging
import re
from importlib import resources
from pathlib import Path

from ..config import AIRPORTS_FILE
from ..domain.airport import Airport

log = logging.getLogger(__name__)

DEFAULT_TZ = "America/Sao_Paulo"
RE_ICAO = re.compile(r"^[A-Z]{4}$")


def _first(row: dict, *keys: str) -> str:
    for k in keys:
        v = row.get(k)
        if v and v.strip():
            return v.strip()
    return ""


class _TzFromCoords:
    """Lazy wrapper around timezonefinder (optional dependency)."""

    def __init__(self) -> None:
        self._tf = None
        self._failed = False

    def __call__(self, lat: str, lon: str) -> str | None:
        if self._failed or not lat or not lon:
            return None
        if self._tf is None:
            try:
                from timezonefinder import TimezoneFinder
                self._tf = TimezoneFinder()
            except ImportError:
                self._failed = True
                log.warning("timezonefinder not installed: airports without tz default to %s", DEFAULT_TZ)
                return None
        try:
            return self._tf.timezone_at(lat=float(lat), lng=float(lon))
        except ValueError:
            return None


class AirportDB:
    def __init__(self) -> None:
        self.by_iata: dict[str, Airport] = {}
        self.by_icao: dict[str, Airport] = {}
        self._tz_from_coords = _TzFromCoords()
        with resources.files("notam_schedule.resources").joinpath("airports.csv").open(encoding="utf-8") as f:
            self._load(f, "built-in")

    @classmethod
    def default(cls, extra: str | Path | None = None) -> "AirportDB":
        """Built-in table + the given file, or data/reference/airports.csv when it exists."""
        db = cls()
        path = Path(extra) if extra else AIRPORTS_FILE
        if path.is_file():
            db.load_extra(path)
        elif extra:
            raise FileNotFoundError(f"Arquivo de aeroportos não encontrado: {extra}")
        return db

    def _load(self, f, source: str) -> int:
        seen_in_file: dict[str, str] = {}
        loaded = 0
        for row in csv.DictReader(f):
            row = {(k or "").strip().lower(): v for k, v in row.items()}
            icao = _first(row, "icao", "ident", "gps_code").upper()
            iata = _first(row, "iata", "iata_code").upper()
            if not RE_ICAO.match(icao):
                continue  # e.g. OurAirports local ids such as "BR-0569"
            if iata and iata in seen_in_file:
                # duplicated IATA in the same file (old and new ICAO): prefer the SB* code
                if not (icao.startswith("SB") and not seen_in_file[iata].startswith("SB")):
                    continue
            known = self.by_icao.get(icao)
            tz = (_first(row, "tz", "timezone")
                  or (known.tz if known and source != "built-in" else "")
                  or self._tz_from_coords(_first(row, "latitude_deg", "lat"), _first(row, "longitude_deg", "lon"))
                  or DEFAULT_TZ)
            name = _first(row, "name", "nome") or (known.name if known else "")
            ap = Airport(iata, icao, name, tz)
            if iata:
                previous = self.by_iata.get(iata)
                if previous and previous.icao != icao and source != "built-in":
                    log.info("IATA %s: %s -> %s (from %s)", iata, previous.icao, icao, source)
                self.by_iata[iata] = ap
                seen_in_file[iata] = icao
            self.by_icao[icao] = ap
            loaded += 1
        return loaded

    def load_extra(self, path: str | Path) -> int:
        """Add/override airports from a user CSV (see module docstring for accepted layouts)."""
        with open(path, encoding="utf-8-sig", newline="") as f:
            n = self._load(f, str(path))
        log.info("Loaded %d airports from %s", n, path)
        return n

    def resolve(self, code: str, strict: bool = False) -> Airport:
        """Accept an IATA (3 letters) or ICAO (4 letters) code.

        Unknown IATA codes return an Airport without ICAO (reported as out of coverage) unless strict.
        """
        c = code.strip().upper()
        if len(c) == 3:
            if c in self.by_iata:
                return self.by_iata[c]
            if strict:
                raise KeyError(
                    f"Aeroporto IATA '{c}' não está na tabela. Inclua-o no data/reference/airports.csv "
                    f"ou passe outro arquivo com --airports."
                )
            return Airport(c, "", "", DEFAULT_TZ)
        if len(c) == 4:
            return self.by_icao.get(c) or Airport("", c, "", DEFAULT_TZ)
        raise KeyError(f"Código de aeroporto inválido: '{code}'")
