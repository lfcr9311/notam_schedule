"""Interfaces the domain needs from the outside world (implemented by adapters)."""
from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from .airport import Runway
from .categories import Classification
from .notam import Notam


class SourceError(RuntimeError):
    """A NOTAM source could not provide data for an airport (API down, bad credentials, missing file)."""


class NotamSource(Protocol):
    """Where NOTAMs and published runways come from (AISWEB API, offline files, a future foreign source).

    Failures must raise SourceError (or a subclass), so callers can report the airport as "no data".
    """

    def notams(self, icao: str) -> list[Notam]: ...

    def runways(self, icao: str) -> list[Runway]: ...


class RawArchive(Protocol):
    """Immutable store of every distinct raw response from a source (the NOTAM history)."""

    def store(self, source: str, area: str, key: str, content: str, fingerprint: str, fetched_at: datetime) -> bool:
        """Save `content` unless the latest stored response for (source, area, key) has the same fingerprint.
        Returns True when a new file was written."""
        ...


@runtime_checkable
class Detector(Protocol):
    """Decides whether a NOTAM restricts a runway. Rules, ML and hybrid detectors implement it."""

    def __call__(self, notam: Notam) -> Classification: ...


@runtime_checkable
class BatchDetector(Detector, Protocol):
    """Detector that is faster when it sees all NOTAMs of an airport first."""

    def prepare(self, notams: list[Notam]) -> None: ...
