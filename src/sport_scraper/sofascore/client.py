from __future__ import annotations

import logging
import random
import threading
import time
from typing import Any

from curl_cffi import requests

from sport_scraper.config import DB_PATH, DEFAULT_SOFA, SofaConfig
from sport_scraper.store.sqlite import Store

log = logging.getLogger("sport_scraper.client")


class CircuitOpen(RuntimeError):
    """Raised when the breaker is open after repeated hard failures."""


class SofaScoreClient:
    """Thread-safe SofaScore HTTP client with retries, backoff and a breaker.

    - curl_cffi sessions are not guaranteed thread-safe -> one Session per thread.
    - Pacing is global (shared lock) so concurrency stays polite.
    - Raw payload logging goes through the Store, which serializes writers.
    """

    def __init__(self, cfg: SofaConfig | None = None, store: Store | None = None, log_raw: bool = True):
        self.cfg = cfg or DEFAULT_SOFA
        self.store = store or Store(DB_PATH)
        self.log_raw = log_raw
        self._local = threading.local()
        self._pace_lock = threading.Lock()
        self._breaker_lock = threading.Lock()
        self._last_request = 0.0
        self._failures = 0
        self._open_until = 0.0

    # -- session ----------------------------------------------------------
    def _session(self) -> Any:
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session(impersonate=self.cfg.impersonate, timeout=self.cfg.timeout)
            s.headers.update(
                {
                    "X-Requested-With": self.cfg.x_requested_with,
                    "Referer": self.cfg.referer,
                    "Origin": self.cfg.origin,
                    "Accept": self.cfg.accept,
                }
            )
            if self.cfg.user_agent:
                s.headers["User-Agent"] = self.cfg.user_agent
            self._local.session = s
        return s

    # -- pacing / breaker -------------------------------------------------
    def _pace(self) -> None:
        """Enforce a *global* minimum interval between requests (if configured).

        The pipeline gets its throughput from a thread pool, so this defaults to
        off; set ``min_interval`` to add a hard politeness floor.
        """
        interval = getattr(self.cfg, "min_interval", 0.0)
        if interval <= 0:
            return
        with self._pace_lock:
            wait = self._last_request + interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()

    def _breaker_check(self) -> None:
        with self._breaker_lock:
            if self._open_until and time.monotonic() < self._open_until:
                raise CircuitOpen(
                    f"circuit open for {self._open_until - time.monotonic():.0f}s "
                    "(upstream refusing requests)"
                )

    def _breaker_record(self, ok: bool, hard: bool) -> None:
        with self._breaker_lock:
            if ok:
                self._failures = 0
                self._open_until = 0.0
                return
            if hard:
                self._failures += 1
                if self._failures >= getattr(self.cfg, "breaker_threshold", 5):
                    self._open_until = time.monotonic() + getattr(self.cfg, "breaker_reset", 60.0)
                    self._failures = 0

    # -- request ----------------------------------------------------------
    def get(self, path: str) -> dict[str, Any]:
        url = path if path.startswith("http") else f"{self.cfg.base_url}{path}"
        self._breaker_check()
        self._pace()

        last_exc: Exception | None = None
        for attempt in range(getattr(self.cfg, "max_retries", 3) + 1):
            try:
                r = self._session().get(url)
            except Exception as exc:  # noqa: BLE001 - network/transport error
                last_exc = exc
                self._backoff(attempt, None)
                continue

            self._log_raw(url, r)
            code = r.status_code

            if code == 200:
                self._breaker_record(ok=True, hard=False)
                return r.json()

            if code in (403, 401):
                # Hard block: trip the breaker, do not hammer.
                self._breaker_record(ok=False, hard=True)
                log.warning("upstream %s for %s (blocked/forbidden)", code, url)
                raise RuntimeError(f"upstream {code} for {url} (blocked/forbidden)")

            if code == 429 or code >= 500:
                self._breaker_record(ok=False, hard=False)
                log.warning("upstream %s for %s; retry %d", code, url, attempt + 1)
                last_exc = RuntimeError(f"upstream {code} for {url}")
                self._backoff(attempt, r.headers.get("retry-after"))
                continue

            # Other 4xx (404 etc.): not retryable.
            raise RuntimeError(f"upstream {code} for {url}")

        assert last_exc is not None
        raise last_exc

    def _backoff(self, attempt: int, retry_after: str | None) -> None:
        base = getattr(self.cfg, "backoff_base", 0.5)
        delay = base * (2**attempt) + random.uniform(0, base)
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        cap = getattr(self.cfg, "backoff_max", 20.0)
        time.sleep(min(delay, cap))

    def _log_raw(self, url: str, r: Any) -> None:
        if not self.log_raw:
            return
        try:
            self.store.insert_raw(
                url, "GET", r.status_code, r.headers.get("content-type"), r.text, int(time.time())
            )
        except Exception:  # noqa: BLE001 - logging must never break a fetch
            pass
