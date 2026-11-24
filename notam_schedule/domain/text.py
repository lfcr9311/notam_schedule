"""Text normalization shared by every layer that reads NOTAM text."""
from __future__ import annotations

import re
import unicodedata


def normalize(text: str) -> str:
    """Uppercase, accents removed and whitespace collapsed."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", t.upper()).strip()
