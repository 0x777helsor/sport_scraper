from __future__ import annotations

from pathlib import Path

from sport_scraper.store.jobs import Jobs
from sport_scraper.store.sqlite import Store

DB = "/tmp/opencode/jobs_test.db"


def main() -> None:
    Path(DB).unlink(missing_ok=True)
    store = Store(DB)
    jobs = Jobs(store)

    a = jobs.enqueue("stats", "12345")
    b = jobs.enqueue("stats", "12345")
    assert a.id == b.id, "dedup failed: two pending jobs for same key"
    print("pending dedup ok:", a.id[:20])

    claimed = jobs.claim_next("w1")
    assert claimed is not None and claimed.id == a.id
    print("claimed:", claimed.id[:20], claimed.status)

    again = jobs.enqueue("stats", "12345")
    assert again.id == a.id, "running job not deduped"
    print("running dedup ok")

    jobs.complete(a.id, {"ss_id": "ss:123"})
    done = jobs.get(a.id)
    assert done is not None and done.status == "done"
    print("done status:", done.status)

    fresh = jobs.enqueue("stats", "12345")
    assert fresh.id != a.id, "should enqueue a fresh job after completion"
    print("re-enqueue after done ->", fresh.id[:20])

    # different key -> different job
    other = jobs.enqueue("stats", "99999")
    assert other.id not in (a.id, fresh.id)
    print("distinct key ok")

    # distinct kinds on same key -> distinct jobs
    odds = jobs.enqueue("odds", "12345")
    assert odds.id != fresh.id
    print("distinct kind ok")

    store.close()
    print("ALL OK")


if __name__ == "__main__":
    main()
