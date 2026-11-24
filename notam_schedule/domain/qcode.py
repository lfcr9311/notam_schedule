"""ICAO Q-code (Doc 8126): the NOTAM office's own coding of subject and condition.

Used as a weak label for ML and as independent confirmation in the confidence score.

Q-code = Q + 2 subject letters + 2 condition letters. E.g. QMRLC (runway closed),
QMDCH (declared distances changed), QFALC (aerodrome closed).
Whether AISWEB returns the Q-code is undocumented, so several fields are inspected.
"""
from __future__ import annotations

import re

from .categories import AERODROME_CLOSED, LENGTH_REDUCTION, NOT_RUNWAY, RUNWAY_CLOSED, SURFACE_CONDITION

RE_Q = re.compile(r"\bQ([A-Z]{4})\b")
RE_PLAIN = re.compile(r"^Q?([A-Z]{4})$")

# Subjects clearly unrelated to the runway itself
NON_RUNWAY_SUBJECT_PREFIX = ("L", "I", "G", "N", "C", "S", "P", "O", "W", "R", "A")
NON_RUNWAY_SUBJECTS = {"MX", "MN", "MP", "MK", "MH", "MM", "MO", "MU", "MY", "FH", "FP", "FU", "FW", "FZ", "FF", "FS"}


def extract_qcode(raw: dict) -> str | None:
    """Return the 4 Q-code letters (without the leading Q), if found."""
    for key in ("q", "qcode", "cod_q", "cod"):
        v = (raw.get(key) or "").strip().upper()
        m = RE_PLAIN.match(v) or RE_Q.search(v)
        if m:
            return m.group(1)
    return None


def label_from_qcode(q: str | None) -> str | None:
    """Weak label from a Q-code; None when inconclusive."""
    if not q or len(q) != 4:
        return None
    subj, cond = q[:2], q[2:]
    if subj == "FA" and cond == "LC":
        return AERODROME_CLOSED
    if subj == "MR":
        if cond == "LC":
            return RUNWAY_CLOSED
        if cond in ("LL", "LT"):
            return LENGTH_REDUCTION
        if cond == "HW":
            return SURFACE_CONDITION
        return None
    if subj in ("MD", "MT"):   # declared distances / threshold
        return LENGTH_REDUCTION
    if subj in ("MW", "MS"):   # strip / stopway: inconclusive
        return None
    if subj in NON_RUNWAY_SUBJECTS or subj[0] in NON_RUNWAY_SUBJECT_PREFIX:
        return NOT_RUNWAY
    return None
