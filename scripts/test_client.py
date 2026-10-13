"""Unit checks for client resilience (no network). Run: python scripts/test_client.py"""
from __future__ import annotations

import dataclasses

from sport_scraper.sofascore.client import SofaScoreClient


class FakeResponse:
    def __init__(self, code: int, retry_after: str = "0"):
        self.status_code = code
        self.headers = {"retry-after": retry_after, "content-type": "application/json"}
        self.text = "{}"

    def json(self):
        return {"ok": True, "code": self.status_code}


class FakeSession:
    """Returns a scripted sequence of status codes; repeats the last one."""

    def __init__(self, codes):
        self._codes = codes if isinstance(codes, list) else [codes]
        self.calls = 0

    def get(self, url):
        self.calls += 1
        code = self._codes[min(self.calls - 1, len(self._codes) - 1)]
        return FakeResponse(code)


def make_client(codes, **cfg_overrides):
    client = SofaScoreClient(log_raw=False)
    client.cfg = dataclasses.replace(
        client.cfg,
        min_interval=0.0,
        backoff_base=0.0,
        max_retries=3,
        breaker_threshold=2,
        breaker_reset=30.0,
        **cfg_overrides,
    )
    fake = FakeSession(codes)
    client._session = lambda: fake  # type: ignore[assignment]
    return client, fake


def test_retry_then_success():
    client, fake = make_client([429, 500, 200])
    out = client.get("/x")
    assert out["code"] == 200
    assert fake.calls == 3, fake.calls
    print("retry -> success ok (3 upstream calls)")


def test_403_trips_breaker():
    client, fake = make_client([403])
    for _ in range(2):  # threshold = 2
        try:
            client.get("/x")
        except RuntimeError:
            pass
    before = fake.calls
    try:
        client.get("/x")
        raise AssertionError("expected the circuit to be open")
    except RuntimeError as exc:
        assert "circuit open" in str(exc), exc
    assert fake.calls == before, "breaker did not suppress the request"
    print("403 storm -> breaker opens ok")


def test_404_not_retried():
    client, fake = make_client([404])
    try:
        client.get("/x")
        raise AssertionError("expected failure")
    except RuntimeError as exc:
        assert "404" in str(exc), exc
    assert fake.calls == 1, fake.calls
    print("404 not retried ok")


def main() -> None:
    test_retry_then_success()
    test_403_trips_breaker()
    test_404_not_retried()
    print("ALL OK")


if __name__ == "__main__":
    main()
