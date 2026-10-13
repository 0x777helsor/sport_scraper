from __future__ import annotations

import time
from typing import Any

from sport_scraper.store.sqlite import Store


class Repo:
    def __init__(self, store: Store):
        self.store = store

    def upsert_event(self, ss_id: str, sport: str, event_id: int, data: dict[str, Any]) -> None:
        self.store.upsert_event(ss_id, sport, event_id, data, int(time.time()))

    def get_event(self, ss_id: str) -> dict[str, Any] | None:
        return self.store.get_event(ss_id)

    def upsert_stats(self, ss_id: str, event_id: int, data: dict[str, Any]) -> None:
        self.store.upsert_stats(ss_id, event_id, data, int(time.time()))

    def get_stats(self, ss_id: str) -> dict[str, Any] | None:
        return self.store.get_stats(ss_id)

    def upsert_odds(self, ss_id: str, event_id: int, data: dict[str, Any]) -> None:
        self.store.upsert_odds(ss_id, event_id, data, int(time.time()))

    def get_odds(self, ss_id: str) -> dict[str, Any] | None:
        return self.store.get_odds(ss_id)
