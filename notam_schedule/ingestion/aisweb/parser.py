"""Parsers for AISWEB XML responses (area=notam and area=rotaer)."""
from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET

from ...domain.airport import Runway
from ...domain.notam import Notam, parse_dtg
from .errors import AiswebError


def parse_notam_xml(content: str | bytes) -> list[Notam]:
    """Parse the AISWEB XML response (area=notam).

    Tolerant parser: any element with an <e> child is treated as a NOTAM.
    """
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else content
    stripped = text.strip()
    if not stripped.startswith("<?xml") and not stripped.startswith("<aisweb"):
        msg = re.sub(r"<[^>]+>", " ", stripped)
        raise AiswebError(f"Resposta inesperada da API AISWEB: {' '.join(msg.split())[:300]}")
    try:
        root = ET.fromstring(stripped.encode("utf-8"))
    except ET.ParseError as exc:
        raise AiswebError(f"XML inválido retornado pela API AISWEB: {exc}") from exc

    notams: list[Notam] = []
    for el in root.iter():
        if el.find("e") is None:
            continue
        raw = {child.tag: (child.text or "").strip() for child in el}
        raw.update({f"@{k}": v for k, v in el.attrib.items()})
        start, _, _ = parse_dtg(raw.get("b"))
        end, perm, est = parse_dtg(raw.get("c"))
        notams.append(
            Notam(
                number=raw.get("n") or raw.get("cod") or raw.get("id", ""),
                loc=(raw.get("loc") or "").upper(),
                ntype=(raw.get("tp") or "N").upper(),
                ref=raw.get("ref", ""),
                start=start,
                end=end,
                permanent=perm,
                end_estimated=est,
                d=raw.get("d", ""),
                e=raw.get("e", ""),
                raw=raw,
            )
        )
    return notams


def parse_rotaer_xml(text: str) -> list[Runway]:
    """Runways published in ROTAER (only type RWY). Malformed XML yields an empty list."""
    try:
        root = ET.fromstring(text.strip().encode("utf-8"))
    except ET.ParseError:
        return []
    out: list[Runway] = []
    for rw in root.iter("runway"):
        if (rw.findtext("type") or "RWY").strip().upper() != "RWY":
            continue
        out.append(Runway(
            ident=(rw.findtext("ident") or "").strip(),
            length=_int(rw.findtext("length")),
            width=_int(rw.findtext("width")),
        ))
    return out


def _int(v: str | None) -> int | None:
    try:
        return int(float((v or "").strip().replace(",", ".")))
    except ValueError:
        return None


_ROOT_AREA_TAG = re.compile(r"<(notam|rotaer)\b[^>]*>")


def response_fingerprint(text: str) -> str:
    """sha256 of the response without the volatile root attributes (per-request id, updatedat) and whitespace.

    Two queries that return the same NOTAMs get the same fingerprint, so the archive stores them once.
    """
    body = _ROOT_AREA_TAG.sub(lambda m: f"<{m.group(1)}>", text, count=1)
    body = re.sub(r">\s+<", "><", body.strip())
    return hashlib.sha256(" ".join(body.split()).encode("utf-8")).hexdigest()
