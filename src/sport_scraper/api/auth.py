from __future__ import annotations

import hashlib
import os
import time

from fastapi import HTTPException, Request, Response

from sport_scraper.config import DB_PATH
from sport_scraper.store.sqlite import Store

WINDOW_SECONDS = 3600  # fixed one-hour window


def auth_enabled() -> bool:
    return os.environ.get("SPORT_SCRAPER_AUTH", "0") not in ("0", "", "false", "False")


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def authenticate(request: Request, response: Response) -> dict:
    """Validate X-API-Key and enforce a fixed-window quota.

    Returns a small context dict. When auth is disabled (default for local dev),
    returns a synthetic anonymous context without touching the DB.

    Emits ``X-RateLimit-*`` headers; over-quota requests get ``429`` with
    ``Retry-After``.
    """
    if not auth_enabled():
        return {"client": "anonymous", "key_id": None}

    raw = request.headers.get("x-api-key")
    if not raw:
        raise HTTPException(status_code=401, detail="missing_api_key")

    store = Store(DB_PATH)
    try:
        row = store.conn.execute(
            "SELECT id, client, quota, revoked FROM api_keys WHERE key_hash=?",
            (_hash(raw),),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=401, detail="invalid_api_key")
        key_id, client, quota, revoked = row
        if revoked:
            raise HTTPException(status_code=403, detail="key_revoked")

        window = int(time.time() // WINDOW_SECONDS) * WINDOW_SECONDS
        store.execute(
            "INSERT INTO key_usage (key_id, window_start, count) VALUES (?,?,1) "
            "ON CONFLICT(key_id, window_start) DO UPDATE SET count = count + 1",
            (key_id, window),
        )
        used = store.conn.execute(
            "SELECT count FROM key_usage WHERE key_id=? AND window_start=?", (key_id, window)
        ).fetchone()[0]
    finally:
        store.close()

    reset = window + WINDOW_SECONDS
    response.headers["X-RateLimit-Limit"] = str(quota)
    response.headers["X-RateLimit-Remaining"] = str(max(0, quota - used))
    response.headers["X-RateLimit-Reset"] = str(reset)

    if used > quota:
        raise HTTPException(
            status_code=429,
            detail="quota_exceeded",
            headers={
                "Retry-After": str(max(1, reset - int(time.time()))),
                "X-RateLimit-Limit": str(quota),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(reset),
            },
        )
    return {"client": client, "key_id": key_id, "used": used, "quota": quota}
