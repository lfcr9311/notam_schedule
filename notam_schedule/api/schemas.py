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


class AirportNotams(BaseModel):
    icao: str = Field(pattern=ICAO_PATTERN)
    status: AirportStatus = Field(description="error/out_of_coverage mean NO DATA, never 'no restriction'")
    error: str | None = None
    runways: list[Runway] = Field(default_factory=list, description="Runways published in ROTAER")
    notams: list[RunwayNotam] = Field(default_factory=list)


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
    open_runways: list[str] | None = Field(None, description="Runways with no closure in the window; null = unknown")
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
