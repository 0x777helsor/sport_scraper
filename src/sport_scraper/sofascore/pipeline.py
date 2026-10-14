from __future__ import annotations

from collections.abc import Sequence
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

# Safety cap on the tournament-index crawl (upstream paginates until
# hasNextPage is false; football needs ~8 pages for a busy day).
MAX_INDEX_PAGES = 40


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
        """Crawl the paged tournament index until ``hasNextPage`` is false.

        The index is paginated (100 items/page) and a busy day for football can
        span ~8 pages; tournaments on later pages are otherwise silently lost.
        IDs are deduped because a tournament can repeat within/across pages.
        """
        ids: list[int] = []
        seen: set[int] = set()
        page = 1
        while page <= MAX_INDEX_PAGES:
            idx = self.ep.tournament_index(sport, d, page=page)
            items = idx.get("scheduled") or []
            if not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                t = item.get("tournament")
                ut = (t or {}).get("uniqueTournament") if isinstance(t, dict) else None
                if isinstance(ut, dict) and "id" in ut and ut["id"] not in seen:
                    seen.add(ut["id"])
                    ids.append(ut["id"])
            if not idx.get("hasNextPage"):
                break
            page += 1
        return ids

    def _fetch_events(self, tid: int, d: str) -> dict[str, Any]:
        try:
            return {"tid": tid, "events": self.ep.tournament_events(tid, d).get("events", [])}
        except Exception as exc:  # noqa: BLE001
            return {"tid": tid, "events": [], "error": str(exc)}

    def _store_events(
        self,
        sport: str,
        events: list[dict[str, Any]],
        with_stats: bool = False,
        with_odds: bool = False,
    ) -> int:
        """Normalize a fetched event batch and upsert it through the one writer.

        Returns the number of events stored. A per-event failure is warned and
        skipped so one malformed payload cannot abort the whole batch.
        """
        n = 0
        for e in events:
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
                n += self._store_events(sport, res["events"], with_stats, with_odds)
        return n

    def warm_tournaments(
        self,
        tournament_ids: Sequence[int],
        days: int = 1,
        start: date | None = None,
        sport: str = "football",
        with_stats: bool = False,
        with_odds: bool = False,
    ) -> dict[str, Any]:
        """Warm an explicit set of ``uniqueTournament`` ids for a date range.

        Targeted counterpart to :meth:`run_days`: instead of crawling the whole
        per-day tournament index (a busy football day lists ~2k tournaments), it
        fetches only the tournaments an operator has mapped. Use it to backfill
        specific leagues/dates cheaply, or to repair a partially completed
        ``sync`` (a long run can be cut off before it reaches a tournament that
        *is* in the index). IDs are deduped; a fetch failure is recorded against
        that (tournament, day) pair without aborting the rest.
        """
        from datetime import timedelta

        order: list[int] = []
        seen: set[int] = set()
        for tid in tournament_ids:
            if tid not in seen:
                seen.add(tid)
                order.append(tid)

        start = start or date.today()
        n_days = max(1, days)
        per_day: dict[str, dict[str, int]] = {}
        total = 0
        errors = 0
        for i in range(n_days):
            ds = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            by_t: dict[str, int] = {}
            for tid in order:
                res = self._fetch_events(tid, ds)
                if res.get("error"):
                    by_t[str(tid)] = -1
                    errors += 1
                    print(f"WARN: events {sport}/{tid} {ds}: {res['error']}")
                    continue
                by_t[str(tid)] = self._store_events(sport, res["events"], with_stats, with_odds)
                total += by_t[str(tid)]
            per_day[ds] = by_t
        return {
            "sport": sport,
            "tournaments": order,
            "start": start.strftime("%Y-%m-%d"),
            "days": n_days,
            "total_events": total,
            "errors": errors,
            "by_day": per_day,
        }

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
        return {
            "date": ds,
            "total_events": sum(v for v in per_sport.values() if v > 0),
            "by_sport": per_sport,
        }

    def run_days(
        self,
        days: int = 1,
        start: date | None = None,
        sports: tuple[str, ...] = SPORTS,
        with_stats: bool = False,
        with_odds: bool = False,
    ) -> dict[str, Any]:
        """Discover ``days`` consecutive dates starting at ``start`` (default today)."""
        from datetime import timedelta

        start = start or date.today()
        per_day: dict[str, Any] = {}
        for i in range(max(1, days)):
            day = start + timedelta(days=i)
            per_day[day.strftime("%Y-%m-%d")] = self.run_day(
                day, sports=sports, with_stats=with_stats, with_odds=with_odds
            )
        return {
            "start": start.strftime("%Y-%m-%d"),
            "days": max(1, days),
            "total_events": sum(v["total_events"] for v in per_day.values()),
            "by_day": per_day,
        }

    def close(self) -> None:
        self.store.close()
