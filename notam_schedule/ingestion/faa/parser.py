"""Parser for FAA NMS-API responses in GeoJSON (GET /v1/notams with header nmsResponseFormat: GEOJSON).

Each feature carries properties.coreNOTAMData.notam (structured fields) and notamTranslation (the ICAO and/or
domestic text). The structured fields win; the ICAO message fills what they lack (item D, the replaced NOTAM).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from ...domain.notam import Notam, parse_dtg
from .errors import FaaError

UTC = timezone.utc
RE_HEADER = re.compile(r"^\s*([A-Z]?\d{1,4}/\d{2,3})\s+NOTAM([NRC])(?:\s+([A-Z]?\d{1,4}/\d{2,3}))?", re.M)
RE_ITEM = re.compile(r"(?:^|\s)([A-GQ])\)\s*")
RE_TEST = re.compile(r"\b(REGRESSION|CLONE TESTING|TEST NOTAM)\b", re.I)
# US military notices (DoD database/DAFIF) repeat the official NOTAM of the aerodrome: not used
SKIP_CLASSIFICATIONS = {"MIL", "MILITARY", "LMIL", "LOCAL_MILITARY"}


def _dt(value: str | None) -> tuple[datetime | None, bool, bool]:
    """ISO 8601 (2025-03-17T17:02:00.000Z), AIXM digits (202503171702) or PERM -> (UTC, permanent, estimated)."""
    v = (value or "").strip().upper()
    if not v:
        return None, False, False
    if "T" in v and "-" in v:
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00").replace("EST", ""))
        except ValueError:
            return None, False, False
        return (dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)), False, "EST" in v
    return parse_dtg(v)


def _items(icao_message: str) -> dict[str, str]:
    """Items Q) A) B) C) D) E) F) G) of an ICAO-format NOTAM."""
    parts = RE_ITEM.split(icao_message)
    out: dict[str, str] = {}
    for key, text in zip(parts[1::2], parts[2::2]):
        out.setdefault(key, " ".join(text.split()))
    return out


def _translation(core: dict, kind: str, field: str) -> str:
    """Text of one translation. The live API sends it in formattedText; spec 1.0.17 shows icao_message etc."""
    for t in core.get("notamTranslation") or []:
        if isinstance(t, dict) and (t.get("type") or "").upper() == kind:
            return (t.get(field) or t.get("formattedText") or "").replace("\r", "")
    return ""


def _number(n: dict) -> str:
    """ICAO style A1234/25 for international NOTAMs; domestic (11/123) and FDC numbers are kept as published."""
    number, series, year = (n.get("number") or "").strip(), (n.get("series") or "").strip(), (n.get("year") or "").strip()
    if not number or "/" in number or not number.isdigit():
        return number
    return f"{series}{number.zfill(4)}/{year[-2:]}" if len(year) == 4 else f"{series}{number}"


def notam_from_feature(feature: dict) -> Notam | None:
    core = (feature.get("properties") or {}).get("coreNOTAMData") or {}
    n = core.get("notam") or {}
    if not n or (n.get("classification") or "").upper() in SKIP_CLASSIFICATIONS:
        return None
    icao_msg = _translation(core, "ICAO", "icao_message")
    items = _items(icao_msg) if icao_msg else {}
    header = RE_HEADER.search(icao_msg) if icao_msg else None
    text = (n.get("text") or items.get("E") or "").replace("\r", "").strip()
    if RE_TEST.search(text):
        return None
    start, _, _ = _dt(n.get("effectiveStart") or items.get("B"))
    end_value = n.get("effectiveEnd") or items.get("C")
    end, perm, est = _dt(end_value)
    est = est or str(n.get("estimated", "")).lower() == "true"
    schedule = items.get("D") or (n.get("schedule") or "").split("~")[-1].strip()
    raw = {
        "id": f"FAA-{(n.get('id') or '').removeprefix('NMS_ID_')}" if n.get("id") else "",
        "source": "faa",
        "q": (n.get("selectionCode") or "").upper(),
        "classification": n.get("classification") or "",
        "status": "I" if (n.get("classification") or "").upper().startswith("INT") else "",
        "fir": n.get("affectedFir") or "",
        "location": n.get("location") or "",
        "issued": n.get("issued") or "",
        "last_updated": n.get("lastUpdated") or "",
        "icao_message": icao_msg,
        "domestic_message": _translation(core, "LOCAL_FORMAT", "domestic_message"),
    }
    return Notam(
        number=_number(n) or (header.group(1) if header else ""),
        loc=(n.get("icaoLocation") or items.get("A") or n.get("location") or "").upper(),
        ntype=(n.get("type") or (header.group(2) if header else "N")).upper()[:1],
        ref=(header.group(3) or "") if header else "",
        start=start,
        end=end,
        permanent=perm or (end_value or "").strip().upper() == "PERM",
        end_estimated=est,
        d=schedule,
        e=text,
        raw=raw,
    )


def parse_nms_geojson(content: str | bytes) -> list[Notam]:
    """NOTAMs of an NMS-API JSON response. Raises FaaError on an error body or invalid JSON."""
    try:
        body = json.loads(content)
    except (ValueError, TypeError) as exc:
        raise FaaError(f"Resposta inválida da FAA (não é JSON): {exc}") from exc
    if not isinstance(body, dict):
        raise FaaError("Resposta inesperada da FAA.")
    if (body.get("status") or "").lower() not in ("", "success"):
        errors = "; ".join(e.get("message", "") for e in body.get("errors") or [] if isinstance(e, dict))
        raise FaaError(f"FAA respondeu {body.get('status')}: {errors or body.get('message', '')}".strip())
    data = body.get("data") or {}
    features = data.get("geojson") if isinstance(data, dict) else None
    if features is None:
        raise FaaError("Resposta da FAA sem o campo data.geojson (cabeçalho nmsResponseFormat: GEOJSON).")
    oldest = datetime.min.replace(tzinfo=UTC)
    latest: dict[tuple[str, str], Notam] = {}
    for n in (notam_from_feature(f) for f in features if isinstance(f, dict)):
        if n is None:
            continue
        # the NMS sometimes holds the same NOTAM twice (re-ingested, new internal id): keep the latest update
        key, updated = (n.loc, n.number), _dt(n.raw["last_updated"])[0] or oldest
        if key not in latest or updated >= (_dt(latest[key].raw["last_updated"])[0] or oldest):
            latest[key] = n
    return list(latest.values())


def response_fingerprint(content: str) -> str:
    """sha256 of the NOTAM set, ignoring key order and whitespace (same NOTAMs -> same fingerprint)."""
    try:
        body = json.loads(content)
        canon = json.dumps(body.get("data", body), sort_keys=True, separators=(",", ":"))
    except (ValueError, AttributeError):
        canon = " ".join(content.split())
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()
