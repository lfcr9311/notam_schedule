"""Configuration: project folders and AISWEB API credentials (environment variables or .env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://aisweb.decea.mil.br/api/"

# Project folders. Anchored to the project root (or $NOTAM_HOME), never to the current directory,
# so the CLI behaves the same when run from cron, an IDE or another folder.
PROJECT_ROOT = Path(os.environ.get("NOTAM_HOME") or Path(__file__).resolve().parent.parent)
DATA_DIR = PROJECT_ROOT / "data"
REFERENCE_DIR = DATA_DIR / "reference"       # airports.csv (IATA -> ICAO, time zone)
INPUT_DIR = DATA_DIR / "input"               # schedule files (malha)
CACHE_DIR = DATA_DIR / "cache"               # latest AISWEB response per airport (overwritten)
RAW_DIR = DATA_DIR / "raw"                   # every distinct AISWEB response, immutable (history)
CORPUS_DB = DATA_DIR / "corpus" / "notams.sqlite"
RUNS_DB = DATA_DIR / "runs" / "runs.sqlite"  # analysis runs and their impacts (for "what changed")
WEB_REPORTS_DIR = DATA_DIR / "reports"      # Excel/CSV of schedules sent through the web page
AIRPORTS_FILE = REFERENCE_DIR / "airports.csv"
OUTPUT_DIR = PROJECT_ROOT / "output"         # reports
REVIEW_FILE = OUTPUT_DIR / "review" / "review.csv"
GOLD_FILE = OUTPUT_DIR / "review" / "gold.csv"
CLUSTERS_FILE = OUTPUT_DIR / "clusters.xlsx"
MODELS_DIR = PROJECT_ROOT / "models"
MODEL_FILE = MODELS_DIR / "current.joblib"
ENV_FILE = PROJECT_ROOT / ".env"


def load_dotenv(path: str | Path = ENV_FILE) -> None:
    """Load a simple .env file (KEY=value) without overriding variables already set."""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


@dataclass(frozen=True)
class AiswebConfig:
    api_key: str
    api_pass: str
    base_url: str = DEFAULT_BASE_URL

    @classmethod
    def from_env(cls) -> "AiswebConfig":
        load_dotenv()
        key = os.environ.get("AISWEB_API_KEY", "").strip()
        pwd = os.environ.get("AISWEB_API_PASS", "").strip()
        if not key or not pwd:
            raise SystemExit(
                "Credenciais da API AISWEB não encontradas. Defina AISWEB_API_KEY e "
                "AISWEB_API_PASS no ambiente ou no arquivo .env (veja .env.example)."
            )
        return cls(key, pwd, os.environ.get("AISWEB_BASE_URL", DEFAULT_BASE_URL))
