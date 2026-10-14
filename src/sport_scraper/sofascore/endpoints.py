from __future__ import annotations

from datetime import date
from typing import Any

from sport_scraper.sofascore.client import SofaScoreClient


class Endpoints:
    def __init__(self, client: SofaScoreClient | None = None):
        self.client = client or SofaScoreClient()

    def tournament_index(
        self, sport: str, d: date | str = date.today(), page: int = 1
    ) -> dict[str, Any]:
        if isinstance(d, date):
            d = d.strftime("%Y-%m-%d")
        return self.client.get(f"/sport/{sport}/scheduled-tournaments/{d}/page/{page}")

    def tournament_events(self, unique_tournament_id: int, d: date | str = date.today()) -> dict[str, Any]:
        if isinstance(d, date):
            d = d.strftime("%Y-%m-%d")
        return self.client.get(f"/unique-tournament/{unique_tournament_id}/scheduled-events/{d}")

    def event(self, event_id: int) -> dict[str, Any]:
        return self.client.get(f"/event/{event_id}")

    def statistics(self, event_id: int) -> dict[str, Any]:
        return self.client.get(f"/event/{event_id}/statistics")

    def odds(self, event_id: int) -> dict[str, Any]:
        return self.client.get(f"/event/{event_id}/odds/1/all")
