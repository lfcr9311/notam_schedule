"""Request and response models of the HTTP API (the contract; exported to contracts/notam-api.openapi.json).

Field names are English (API convention); human-readable labels and reasons are Portuguese, like the reports.
All times are UTC (ISO 8601 with offset).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Operation = Literal["departure", "arrival"]
AirportStatus = Literal["ok", "error", "out_of_coverage"]
ICAO_PATTERN = r"^[A-Z]{4}$"


class Period(BaseModel):
    start: datetime
    end: datetime


class Confidence(BaseModel):
    level: Literal["ALTA", "MÉDIA", "BAIXA"]
    score: int = Field(ge=0, le=100)
    reasons: list[str] = Field(description="What lowered (or supports) the score, in Portuguese")


class Closure(BaseModel):
    type: Literal["total", "conditional"] = Field(
        description="conditional = operations may be authorised with prior coordination / PPR")
    operations: Literal["both", "landing", "takeoff"] = Field(description="Operations the closure applies to")
    exception: str | None = Field(None, description="The EXC clause of the NOTAM, e.g. 'INT RWY 03/21'")


class Runway(BaseModel):
    ident: str = Field(examples=["15/33"])
    length_m: int | None = None
    width_m: int | None = None


class RunwayNotam(BaseModel):
    id: str = Field(description="Stable id: ICAO|number", examples=["SBSV|B2126/26"])
    number: str = Field(examples=["B2126/26"])
    airport: str = Field(pattern=ICAO_PATTERN)
    category: str = Field(examples=["RUNWAY_CLOSED"])
    category_label: str = Field(examples=["Pista fechada"])
    description: str = Field(description="Plain-language summary (Portuguese)")
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] = Field(description="Severity of the NOTAM on its own")
    runways: list[str] = Field(description="Runways named in the NOTAM", examples=[["17/35"]])
    closure: Closure | None = Field(None, description="Present for runway and aerodrome closures")
    valid_from: datetime | None = Field(None, description="Item B")
    valid_to: datetime | None = Field(None, description="Item C; null when permanent")
    permanent: bool
    valid_to_estimated: bool
    schedule: str = Field(description="Item D as published (weekdays are UTC days)")
    schedule_understood: bool = Field(description="false: item D not parsed, NOTAM treated as active all the time")
    active_periods: list[Period] = Field(description="When the NOTAM is active inside the requested window")
    text: str = Field(description="Item E as published")
    qcode: str | None = Field(None, examples=["MRLC"])
    detection: Literal["rules", "ml", "rules+ml"]
    ml_probability: float | None = None
    confidence: Confidence
    note: str = ""
    duplicates: list[str] = Field(default_factory=list, description="Same NOTAM published in another language")


class OtherNotam(BaseModel):
    """A NOTAM that is not a runway restriction (taxiway, lights, navaids, obstacles, airspace...)."""
    id: str
    number: str
    airport: str
    qcode: str | None = Field(None, description="Q-code letters without the leading Q (subject + condition)")
    valid_from: datetime | None
    valid_to: datetime | None
    permanent: bool
    valid_to_estimated: bool
    schedule: str = Field(description="Item D as published")
    schedule_understood: bool
    active_periods: list[Period]
    text: str
    note: str = ""


class AirportNotams(BaseModel):
    icao: str = Field(pattern=ICAO_PATTERN)
    name: str | None = None
    timezone: str | None = Field(None, description="IANA time zone of the airport, for local times", examples=["America/Bahia"])
    source: str | None = Field(None, description="NOTAM provider: AISWEB (Brazil) or FAA (NMS-API, outside Brazil)")
    status: AirportStatus = Field(description="error/out_of_coverage mean NO DATA, never 'no restriction'")
    error: str | None = None
    runways: list[Runway] = Field(default_factory=list,
                                  description="Runways published in ROTAER (Brazil only; empty for FAA airports)")
    notams: list[RunwayNotam] = Field(default_factory=list)
    other_notams: list[OtherNotam] = Field(default_factory=list,
                                           description="Only with include_other=true: every non-runway NOTAM")


class NotamsResponse(BaseModel):
    generated_at: datetime
    window: Period
    categories: list[str]
    airports: list[AirportNotams]


class Leg(BaseModel):
    id: str | None = Field(None, description="Caller's identifier, echoed back (e.g. 'AZU4787-20261201-DEP')")
    airport: str = Field(pattern=ICAO_PATTERN, examples=["SBSV"])
    operation: Operation
    time: datetime = Field(description="STD for departures, STA for arrivals. Without offset = UTC")

    @field_validator("airport", mode="before")
    @classmethod
    def _upper(cls, v):
        return v.strip().upper() if isinstance(v, str) else v


class RunwayCheckRequest(BaseModel):
    legs: list[Leg] = Field(min_length=1, max_length=5000)
    margin_minutes: int = Field(60, ge=0, le=360,
                                description="Window before and after the leg time; 0 = only the leg time itself")
    categories: list[str] | None = Field(None, description="Default: RUNWAY_CLOSED and AERODROME_CLOSED")


class LegNotam(BaseModel):
    id: str
    number: str
    category: str
    description: str
    severity: str
    runways: list[str]
    closure: Closure | None = None
    active_periods: list[Period] = Field(description="Active periods overlapping the leg window")
    confidence: Confidence = Field(description="Confidence of this NOTAM for this leg (includes timing)")


class LegResult(BaseModel):
    id: str | None = None
    airport: str
    operation: Operation
    time: datetime
    status: AirportStatus
    error: str | None = None
    restricted: bool = Field(description="At least one closure applies to this operation in the window")
    severity: str | None = Field(None, description="Worst severity for the leg (CRITICAL when no runway is left)")
    no_runway: bool = Field(description="At some moment of the window every published runway is closed")
    open_runways: list[str] | None = Field(None, description="Runways with no closure in the window; null = runways unknown (no ROTAER)")
    notams: list[LegNotam] = Field(default_factory=list)


class RunwayCheckResponse(BaseModel):
    generated_at: datetime
    margin_minutes: int
    categories: list[str]
    legs: list[LegResult]


class Health(BaseModel):
    status: Literal["ok"]
    version: str
    source: Literal["aisweb", "offline"]
    international: bool = Field(False, description="Whether NOTAMs outside Brazil (FAA NMS-API) are enabled")


# ---------------------------------------------------------------- web page (public /web/* endpoints)
class ImpactRow(BaseModel):
    date: str = Field(description="Flight date (ISO)")
    flight: str
    origin: str
    destination: str
    operation: Operation
    airport: str
    timezone: str
    time: datetime | None = Field(None, description="STD/STA in UTC; null = flight without time (whole day)")
    severity: str
    category: str
    description: str
    notam: str
    closure: Closure | None = None
    open_runways: list[str] | None = None
    no_runway: bool
    confidence: Confidence


class DaySummary(BaseModel):
    date: str
    flights: int
    impacted: int
    critical: int
    airports: list[str]


class ChangeRow(BaseModel):
    kind: Literal["NOVO", "SEVERIDADE MUDOU", "JANELA MUDOU", "ENCERRADO"]
    date: str
    flight: str
    origin: str
    destination: str
    operation: Operation
    airport: str
    notam: str
    category: str
    severity_before: str | None = None
    severity_after: str | None = None
    window_before: Period | None = None
    window_after: Period | None = None


class RunInfo(BaseModel):
    id: int
    started_at: datetime
    schedule_file: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    flights: int | None = None
    impacts: int | None = None


class ChangeCounts(BaseModel):
    new: int = 0
    severity_changed: int = 0
    window_changed: int = 0
    ended: int = 0


class ChangesResponse(BaseModel):
    run: RunInfo | None = Field(None, description="Run whose changes are shown (default: the latest)")
    previous: RunInfo | None = Field(None, description="Run it is compared with; null = first run, nothing to compare")
    counts: ChangeCounts
    changes: list[ChangeRow]
    runs: list[RunInfo] = Field(description="Most recent runs, to pick another one")


class Downloads(BaseModel):
    xlsx: str
    csv: str


class ScheduleResult(BaseModel):
    run_id: int
    schedule_file: str
    time_basis: str = Field(description="utc / local (with ' (auto)' when detected)")
    period: Period
    categories: list[str]
    flights: int
    impacted_flights: int
    critical_flights: int
    by_day: list[DaySummary]
    impacts: list[ImpactRow] = Field(description="Flight x NOTAM impacts (at most 2000, most severe first)")
    impacts_total: int
    errors: dict[str, str] = Field(description="Airports with no NOTAM data (query failed)")
    out_of_coverage: list[str]
    changes: ChangesResponse
    downloads: Downloads



class DigestInfo(BaseModel):
    """A NOTAM digest (the e-mail sent to the analysts through the RSS feed)."""
    id: int
    title: str
    generated_at: datetime
    page: str = Field(description="The digest as a web page (Excel link inside)")
    feed: str = Field(description="RSS feed that Power Automate reads")


# ---------------------------------------------------------------- NOTAM inbox (watched airports, read marks)
InboxState = Literal["unread", "read", "all"]


class InboxNotam(BaseModel):
    id: int
    icao: str
    number: str
    source: str
    runway: bool = Field(description="Runway/aerodrome restriction")
    kind: str
    severity: str
    runways: list[str]
    valid_from: datetime | None
    valid_to: datetime | None
    permanent: bool
    estimated: bool
    schedule: str
    text: str
    first_seen: datetime
    read_at: datetime | None
    read_by: str | None


class InboxAirport(BaseModel):
    icao: str
    name: str
    last_sync_at: datetime | None
    last_error: str | None = Field(None, description="Last query failed: its NOTAMs may be out of date")
    unread: int
    total: int


class InboxCounts(BaseModel):
    unread: int
    read: int
    all: int


class InboxResponse(BaseModel):
    state: InboxState
    counts: InboxCounts
    airports: list[InboxAirport]
    notams: list[InboxNotam]


class ReadRequest(BaseModel):
    by: str = Field(min_length=1, max_length=80, description="Who read it (name shown to the team)")


class AirportAdd(BaseModel):
    icao: str = Field(description="ICAO (SBSV) or IATA (SSA)")


class SyncInfo(BaseModel):
    synced_at: datetime
    airports: int
    notams: int
    new: int
    ended: int
    errors: dict[str, str]
