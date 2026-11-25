"""One NotamSource over two providers: AISWEB (DECEA) for Brazil and the FAA NMS-API for the rest of the world."""
from __future__ import annotations

from ..domain.airport import BR_PREFIXES, Runway
from ..domain.notam import Notam
from ..domain.ports import NotamSource, SourceError


class RoutedSource:
    """Brazilian ICAOs (SB, SD, SI, SJ, SN, SS, SW) go to `brazil`; any other to `international` when set."""

    def __init__(self, brazil: NotamSource, international: NotamSource | None = None) -> None:
        self.brazil = brazil
        self.international = international

    @staticmethod
    def is_brazilian(icao: str) -> bool:
        return icao.upper().startswith(BR_PREFIXES)

    def _pick(self, icao: str) -> NotamSource:
        if self.is_brazilian(icao):
            return self.brazil
        if self.international is None:
            raise SourceError(f"{icao} fora do Brasil e a fonte internacional (FAA) não está configurada.")
        return self.international

    def covers(self, icao: str) -> bool:
        if self.is_brazilian(icao):
            return True
        if self.international is None:
            return False
        fn = getattr(self.international, "covers", None)
        return bool(fn(icao)) if callable(fn) else True

    def name_for(self, icao: str) -> str:
        return "AISWEB" if self.is_brazilian(icao) else "FAA"

    def notams(self, icao: str) -> list[Notam]:
        return self._pick(icao).notams(icao)

    def runways(self, icao: str) -> list[Runway]:
        return self._pick(icao).runways(icao) if self.covers(icao) else []
