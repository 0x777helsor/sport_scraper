from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("SPORT_SCRAPER_DATA", BASE_DIR / "data"))
RAW_DIR = DATA_DIR / "raw"
DB_PATH = Path(os.environ.get("SPORT_SCRAPER_DB", DATA_DIR / "sport_scraper.db"))


@dataclass(slots=True)
class SofaConfig:
    base_url: str = os.environ.get("SOFA_BASE_URL", "https://www.sofascore.com/api/v1")
    impersonate: str = os.environ.get("SOFA_IMPERSONATE", "chrome")
    referer: str = "https://www.sofascore.com/"
    origin: str = "https://www.sofascore.com"
    x_requested_with: str = "XMLHttpRequest"
    accept: str = "application/json, text/plain, */*"
    timeout: float = 25.0
    user_agent: str | None = None
    # Politeness / resilience.
    min_interval: float = 0.0  # global min seconds between requests (0 = off)
    max_retries: int = 3
    backoff_base: float = 0.5
    backoff_max: float = 20.0
    breaker_threshold: int = 5
    breaker_reset: float = 60.0


DEFAULT_SOFA = SofaConfig()
