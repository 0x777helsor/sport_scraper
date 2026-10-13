from __future__ import annotations

import time
from typing import Any

from curl_cffi import requests

PENDING = {"pending", "running"}


class SportScraperProvider:
    """Reusable client for the sport_scraper HTTP API.

    Hoping to consume the provider? This is the seam. Upstream credentials and
    scraping never leak to the consumer: you talk to the read-only API through
    this adapter, which transparently handles the 202 -> poll -> 200 contract.

    Usage:
        p = SportScraperProvider("http://127.0.0.1:8081", api_key="ssk_...")
        ev = p.get_event("ss:15238207")          # blocks until warm (or timeout)
        stats = p.get_statistics("ss:15238207")
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8081",
        api_key: str | None = None,
        timeout: float = 20.0,
        poll_interval: float = 0.5,
        max_wait: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.poll_interval = poll_interval
        self.max_wait = max_wait

    def _headers(self) -> dict[str, str]:
        h = {"Accept": "application/json"}
        if self.api_key:
            h["X-API-Key"] = self.api_key
        return h

    def _get(self, path: str) -> requests.Response:
        return requests.get(f"{self.base_url}{path}", headers=self._headers(), timeout=self.timeout)

    def _await(self, response: requests.Response) -> dict[str, Any]:
        """Resolve a 200 or drive a 202 to completion."""
        if response.status_code == 200:
            return response.json()
        if response.status_code == 202:
            job = response.json()
            job_id = job["job_id"]
            deadline = time.monotonic() + self.max_wait
            while time.monotonic() < deadline:
                jr = self._get(f"/v1/jobs/{job_id}")
                jr.raise_for_status()
                status = jr.json()
                if status["status"] == "done":
                    # job finished; the underlying resource is now cached
                    return {"__refetch__": True, "job": status}
                if status["status"] == "error":
                    raise RuntimeError(f"job {job_id} failed: {status.get('error')}")
                time.sleep(self.poll_interval)
            return {"__pending__": True, "job_id": job_id}
        response.raise_for_status()
        raise RuntimeError(f"unexpected status {response.status_code}")

    def _fetch(self, path: str) -> dict[str, Any]:
        r = self._get(path)
        result = self._await(r)
        if isinstance(result, dict) and result.get("__refetch__"):
            r2 = self._get(path)
            r2.raise_for_status()
            return r2.json()
        if isinstance(result, dict) and result.get("__pending__"):
            raise TimeoutError(f"data not ready within {self.max_wait}s (job {result['job_id']})")
        return result

    # --- public API -------------------------------------------------------
    def get_event(self, ss_id: str) -> dict[str, Any]:
        return self._fetch(f"/v1/events/{ss_id}")

    def get_statistics(self, ss_id: str) -> dict[str, Any]:
        return self._fetch(f"/v1/events/{ss_id}/statistics")

    def get_odds(self, ss_id: str) -> dict[str, Any]:
        return self._fetch(f"/v1/events/{ss_id}/odds")

    def job_status(self, job_id: str) -> dict[str, Any]:
        r = self._get(f"/v1/jobs/{job_id}")
        r.raise_for_status()
        return r.json()

    def health(self) -> dict[str, Any]:
        r = self._get("/v1/health")
        r.raise_for_status()
        return r.json()
