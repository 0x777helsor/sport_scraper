from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Response

from sport_scraper.api.auth import auth_enabled, authenticate
from sport_scraper.config import DB_PATH
from sport_scraper.sofascore.sports import SPORT_SLUGS
from sport_scraper.store.jobs import Jobs
from sport_scraper.store.sqlite import Store
from sport_scraper.worker.runner import KIND_EVENT, KIND_ODDS, KIND_STATS

app = FastAPI(title="sport_scraper API", version="v1")


def _store() -> Store:
    return Store(DB_PATH)


@app.get("/v1/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/v1/meta")
def meta() -> dict:
    """Operational view: store counts, queue depth, auth state."""
    s = _store()
    try:
        counts = s.counts()
        depth = Jobs(s).depth()
    finally:
        s.close()
    return {
        "sports": list(SPORT_SLUGS),
        "auth_enabled": auth_enabled(),
        "counts": counts,
        "queue": depth,
    }


@app.get("/v1/sports/{sport}/events")
def list_events(
    sport: str,
    response: Response,
    on: str | None = Query(default=None, description="UTC date YYYY-MM-DD"),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _ctx: dict = Depends(authenticate),
) -> Any:
    """List cached fixtures/results for a sport (optionally for one UTC day)."""
    if sport not in SPORT_SLUGS:
        raise HTTPException(status_code=404, detail=f"unknown_sport:{sport}")
    start_ts = end_ts = None
    if on:
        try:
            d = date.fromisoformat(on)
        except ValueError:
            raise HTTPException(status_code=400, detail="bad_date") from None
        start_ts = int(datetime.combine(d, time.min, tzinfo=timezone.utc).timestamp())
        end_ts = int(datetime.combine(d, time.max, tzinfo=timezone.utc).timestamp())
    s = _store()
    try:
        events = s.list_events(sport=sport, start_ts=start_ts, end_ts=end_ts, limit=limit, offset=offset)
    finally:
        s.close()
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)
    return {"sport": sport, "date": on, "count": len(events), "events": events}


def _serve_cached_or_enqueue(
    ss_id: str,
    kind: str,
    getter,  # callable[[Store], dict | None]
    response: Response,
) -> Any:
    """Read from the store; on miss, enqueue a deduped job and return 202.

    Contract: cache hit -> 200 body; miss -> 202 + Location: /v1/jobs/{id}.
    """
    s = _store()
    try:
        cached = getter(s)
        if cached is not None:
            return cached
        event_id = ss_id.split(":", 1)[-1]
        job = Jobs(s).enqueue(kind, event_id)
    finally:
        s.close()
    response.status_code = 202
    response.headers["Location"] = f"/v1/jobs/{job.id}"
    return {"job_id": job.id, "status": job.status, "location": f"/v1/jobs/{job.id}"}


@app.get("/v1/events/{ss_id}")
def get_event(ss_id: str, response: Response, _ctx: dict = Depends(authenticate)) -> Any:
    return _serve_cached_or_enqueue(ss_id, KIND_EVENT, lambda s: s.get_event(ss_id), response)


@app.get("/v1/events/{ss_id}/statistics")
def get_stats(ss_id: str, response: Response, _ctx: dict = Depends(authenticate)) -> Any:
    return _serve_cached_or_enqueue(ss_id, KIND_STATS, lambda s: s.get_stats(ss_id), response)


@app.get("/v1/events/{ss_id}/odds")
def get_odds(ss_id: str, response: Response, _ctx: dict = Depends(authenticate)) -> Any:
    return _serve_cached_or_enqueue(ss_id, KIND_ODDS, lambda s: s.get_odds(ss_id), response)


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, _ctx: dict = Depends(authenticate)) -> dict:
    s = _store()
    try:
        job = Jobs(s).get(job_id)
    finally:
        s.close()
    if job is None:
        raise HTTPException(status_code=404, detail="job_not_found")
    return {
        "id": job.id,
        "kind": job.kind,
        "key": job.key,
        "status": job.status,
        "error": job.error,
        "result": job.result,
        "attempts": job.attempts,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }
