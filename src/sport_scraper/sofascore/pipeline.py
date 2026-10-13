from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date
from typing import Any

from sport_scraper.config import DB_PATH
from sport_scraper.sofascore.client import SofaScoreClient
from sport_scraper.sofascore.endpoints import Endpoints
from sport_scraper.sofascore.normalize import normalize_event, normalize_statistics
from sport_scraper.store.repo import Repo
from sport_scraper.store.sqlite import Store

SPORTS = ("football", "basketball", "baseball", "american-football", "tennis", "cricket")

# Concurrency is for *reads* only. Writes still funnel through one Store.
FETCH_CONCURRENCY = 8


class Pipeline:
    """Discovery pipeline: tournament index -> events -> (optional) details.

    Stats/odds are intentionally *off* by default. They are expensive and are
    fetched on demand by the worker when a read misses (see api/main.py).
    """

    def __init__(self, max_fetch_workers: int = FETCH_CONCURRENCY) -> None:
        self.client = SofaScoreClient()
        self.ep = Endpoints(self.client)
        self.store = Store(DB_PATH)
        self.repo = Repo(self.store)
        self.max_fetch_workers = max_fetch_workers

    @staticmethod
    def ss_id(event_id: int) -> str:
        return f"ss:{event_id}"

    def _tournament_ids(self, sport: str, d: str) -> list[int]:
        idx = self.ep.tournament_index(sport, d)
        ids: list[int] = []
        for item in idx.get("scheduled", []) or []:
            if not isinstance(item, dict):
                continue
            t = item.get("tournament")
            ut = (t or {}).get("uniqueTournament") if isinstance(t, dict) else None
            if isinstance(ut, dict) and "id" in ut:
                ids.append(ut["id"])
        return ids

    def _fetch_events(self, tid: int, d: str) -> dict[str, Any]:
        try:
            return {"tid": tid, "events": self.ep.tournament_events(tid, d).get("events", [])}
        except Exception as exc:  # noqa: BLE001
            return {"tid": tid, "events": [], "error": str(exc)}

    def discover_sport(
        self,
        sport: str,
        d: str,
        with_stats: bool = False,
        with_odds: bool = False,
    ) -> int:
        tids = self._tournament_ids(sport, d)
        n = 0
        with ThreadPoolExecutor(max_workers=self.max_fetch_workers) as pool:
            for res in pool.map(lambda t: self._fetch_events(t, d), tids):
                if res.get("error"):
                    print(f"WARN: events {sport}/{res['tid']}: {res['error']}")
                    continue
                for e in res["events"]:
                    try:
                        ne = normalize_event(e, sport)  # type: ignore[arg-type]
                        eid = ne["id"]
                        self.repo.upsert_event(self.ss_id(eid), ne["sport"], eid, ne)
                        n += 1
                        if with_stats:
                            try:
                                self.repo.upsert_stats(
                                    self.ss_id(eid), eid, normalize_statistics(self.ep.statistics(eid))
                                )
                            except Exception:
                                pass
                        if with_odds:
                            try:
                                self.repo.upsert_odds(self.ss_id(eid), eid, self.ep.odds(eid))
                            except Exception:
                                pass
                    except Exception as exc:  # noqa: BLE001
                        print(f"WARN: normalize {sport}/{e.get('id')}: {exc}")
        return n

    def run_day(
        self,
        d: date | None = None,
        sports: tuple[str, ...] = SPORTS,
        with_stats: bool = False,
        with_odds: bool = False,
    ) -> dict[str, Any]:
        d = d or date.today()
        ds = d.strftime("%Y-%m-%d")
        per_sport: dict[str, int] = {}
        for sport in sports:
            try:
                per_sport[sport] = self.discover_sport(sport, ds, with_stats, with_odds)
            except Exception as exc:  # noqa: BLE001
                per_sport[sport] = -1
                print(f"WARN: index {sport}: {exc}")
        self.store.close()
        return {"date": ds, "total_events": sum(v for v in per_sport.values() if v > 0), "by_sport": per_sport}
