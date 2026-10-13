from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from sport_scraper.config import DB_PATH
from sport_scraper.sofascore.endpoints import Endpoints
from sport_scraper.sofascore.normalize import normalize_event, normalize_statistics
from sport_scraper.store.jobs import Jobs
from sport_scraper.store.repo import Repo
from sport_scraper.store.sqlite import Store

log = logging.getLogger("sport_scraper.worker")

# Job kinds
KIND_EVENT = "event"
KIND_STATS = "stats"
KIND_ODDS = "odds"


class Worker:
    """Single-writer worker that drains the job queue.

    The API never touches upstream; it only enqueues jobs and reads the store.
    """

    def __init__(self) -> None:
        self.store = Store(DB_PATH)
        self.jobs = Jobs(self.store)
        self.repo = Repo(self.store)
        self.ep = Endpoints()

    def _execute(self, kind: str, key: str) -> dict[str, Any]:
        # key is the event id as a string
        eid = int(key)
        ss_id = f"ss:{eid}"
        if kind == KIND_EVENT:
            raw = self.ep.event(eid)
            data = normalize_event(raw.get("event", raw))
            self.repo.upsert_event(ss_id, data.get("sport") or "unknown", eid, data)
            return {"ss_id": ss_id}
        if kind == KIND_STATS:
            raw = self.ep.statistics(eid)
            self.repo.upsert_stats(ss_id, eid, normalize_statistics(raw))
            return {"ss_id": ss_id}
        if kind == KIND_ODDS:
            raw = self.ep.odds(eid)
            self.repo.upsert_odds(ss_id, eid, raw)
            return {"ss_id": ss_id}
        raise ValueError(f"unknown job kind: {kind}")

    def run_once(self, owner: str | None = None) -> bool:
        owner = owner or f"worker_{uuid.uuid4().hex[:8]}"
        job = self.jobs.claim_next(owner)
        if job is None:
            return False
        started = time.monotonic()
        log.info("job start id=%s kind=%s key=%s attempt=%d", job.id, job.kind, job.key, job.attempts)
        try:
            result = self._execute(job.kind, job.key)
            self.jobs.complete(job.id, result)
            log.info(
                "job done id=%s kind=%s key=%s in %.2fs",
                job.id, job.kind, job.key, time.monotonic() - started,
            )
        except Exception as exc:  # noqa: BLE001
            self.jobs.fail(job.id, f"{type(exc).__name__}: {exc}")
            log.warning(
                "job error id=%s kind=%s key=%s: %s: %s",
                job.id, job.kind, job.key, type(exc).__name__, exc,
            )
        return True

    def run_forever(self, poll_seconds: float = 1.0) -> None:
        log.info("worker polling (poll=%.1fs)", poll_seconds)
        while True:
            did = self.run_once()
            if not did:
                time.sleep(poll_seconds)

    def close(self) -> None:
        self.store.close()
