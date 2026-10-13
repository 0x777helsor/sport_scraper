from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class StoredEvent:
    ss_id: str  # "ss:<id>"
    sport: str
    event_id: int
    data: dict[str, Any]


@dataclass(slots=True)
class StoredStats:
    ss_id: str
    event_id: int
    data: dict[str, Any]


@dataclass(slots=True)
class StoredOdds:
    ss_id: str
    event_id: int
    data: dict[str, Any]
