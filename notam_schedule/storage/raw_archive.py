"""Immutable archive of raw source responses: the NOTAM history.

The AISWEB only returns NOTAMs in force, and the cache is overwritten on every query. This archive keeps
every distinct response, gzip-compressed, so the corpus can be rebuilt and parsers/models re-run later:

    data/raw/<source>/<area>/<KEY>/<YYYYMMDDTHHMMSSZ>.xml.gz
    data/raw/<source>/<area>/<KEY>/latest.sha256      fingerprint of the newest file

Files are never modified or deleted. Implements domain.ports.RawArchive.
"""
from __future__ import annotations

import gzip
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from ..config import RAW_DIR


class FileRawArchive:
    def __init__(self, root: str | Path = RAW_DIR) -> None:
        self.root = Path(root)

    def store(self, source: str, area: str, key: str, content: str, fingerprint: str, fetched_at: datetime) -> bool:
        folder = self.root / source / area / key
        latest = folder / "latest.sha256"
        if latest.is_file() and latest.read_text().strip() == fingerprint:
            return False
        folder.mkdir(parents=True, exist_ok=True)
        stamp = fetched_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = folder / f"{stamp}.xml.gz"
        n = 1
        while path.exists():  # two distinct responses in the same second: never overwrite
            path, n = folder / f"{stamp}_{n}.xml.gz", n + 1
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(content)
        latest.write_text(fingerprint + "\n")
        return True

    def files(self, source: str = "aisweb", area: str = "notam") -> Iterator[Path]:
        """Archived responses in chronological order per key."""
        yield from sorted((self.root / source / area).glob("*/*.xml.gz"))


def read_archived(path: str | Path) -> str:
    p = Path(path)
    if p.suffix == ".gz":
        with gzip.open(p, "rt", encoding="utf-8") as f:
            return f.read()
    return p.read_text(encoding="utf-8")
