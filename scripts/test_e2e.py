"""End-to-end check of the 202 -> worker -> 200 contract.

Fresh DB + one real event id exercises the whole path:
  1. GET (miss)      -> 202 + Location
  2. N identical misses dedup to ONE job
  3. worker runs it  -> job done, payload stored
  4. GET (hit)       -> 200 + payload
  5. stats/odds miss -> 202
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Response

from sport_scraper.api import main as api
from sport_scraper.store.jobs import Jobs
from sport_scraper.worker import runner

EVENT_ID = 15238207  # a finished football match seen in the Phase 0 samples
TEST_DB = Path("/tmp/opencode/e2e.db")


def get(path_fn, ss_id):
    resp = Response()
    body = path_fn(ss_id, resp)
    return resp.status_code, body


def main() -> None:
    TEST_DB.unlink(missing_ok=True)
    api.DB_PATH = TEST_DB
    runner.DB_PATH = TEST_DB

    status, body = get(api.get_event, f"ss:{EVENT_ID}")
    assert status == 202, f"expected 202, got {status}"
    job_id = body["job_id"]
    print("1. miss GET -> 202", body["location"])

    s = api._store()
    jobs = Jobs(s)
    assert jobs.enqueue("event", str(EVENT_ID)).id == job_id
    assert jobs.enqueue("event", str(EVENT_ID)).id == job_id
    s.close()
    print("2. N identical misses -> one job:", job_id[:20])

    w = runner.Worker()
    assert w.run_once("test-worker") is True
    job = Jobs(w.store).get(job_id)
    print("3. worker -> job status:", job.status)
    assert job is not None and job.status == "done"
    w.store.close()

    status, body = get(api.get_event, f"ss:{EVENT_ID}")
    assert status == 200, f"expected 200 after warm, got {status}"
    print("4. warm GET -> 200, id =", body.get("id"))

    for fn, label in ((api.get_stats, "statistics"), (api.get_odds, "odds")):
        st, _ = get(fn, f"ss:{EVENT_ID}")
        assert st == 202, f"{label} expected 202, got {st}"
        print(f"5. {label} cold GET -> 202")

    # Drain the remaining jobs and confirm stats/odds become warm.
    w = runner.Worker()
    while w.run_once("test-worker"):
        pass
    w.store.close()
    for fn, label in ((api.get_stats, "statistics"), (api.get_odds, "odds")):
        st, body = get(fn, f"ss:{EVENT_ID}")
        assert st == 200, f"{label} expected 200 after drain, got {st}"
        print(f"6. {label} warm GET -> 200")

    print("ALL OK")


if __name__ == "__main__":
    main()
