# Progress

_Last updated: 2026-10-13_

## One-liner
Worker-warmed, read-only SofaScore provider for six sports: upstream scraping
lives entirely in a background worker; the FastAPI service only reads the local
SQLite store and queues misses as jobs.

## Phase status

| Phase | Status | Evidence |
|---|---|---|
| 0 — Access gate | ✅ passed | `scripts/phase0_probe.py` exits 0 via `www.sofascore.com/api/v1` |
| 1 — Scraper core | ✅ complete | two-step discovery, concurrent fetch, hardened client, discovery log |
| 2 — Worker + queue | 🟡 | dedup queue + lease-based worker; `202` flow verified |
| 3 — Read API | 🟡 | `/v1/health`, `/v1/meta`, `/v1/sports/{sport}/events`, event/stats/odds, jobs |
| 4 — Auth + quotas | 🟡 | sha256 keys, revoke, fixed-window quota (`401`→`200`→`429` verified) |
| 5 — Provider adapter | 🟡 | `SportScraperProvider` drives a live API cold→warm |
| 6 — Containerize | 🟡 | Dockerfile + compose + backup/restore scripts (build pending) |
| 7 — Harden | ⬜ | retries/backoff/breaker done; structured logging + runbook pending |

## Verified behaviours

- Full six-sport sync for one date: **~81s, ~2,700 events** (8 concurrent
  fetchers, single writer).
- Store: 2,327 events / 227 statistics / 254 odds / ~3,588 raw payloads.
- `202 → worker → 200`: verified over HTTP and through the provider adapter.
- Job dedup: N identical misses ⇒ **one** job, **one** upstream fetch.
- Quota: 4th request over a quota of 3 returns **429**; missing key **401**.
- Backup/restore round-trip: event/raw counts identical.
- Client: 429/5xx retried with backoff; 403 storm trips the breaker; 404 not retried.

## Test commands

```bash
.venv/bin/python3 scripts/test_jobs.py     # queue dedup + lifecycle
.venv/bin/python3 scripts/test_e2e.py      # 202 -> worker -> 200
.venv/bin/python3 scripts/test_client.py   # retry/backoff/breaker
.venv/bin/python3 scripts/phase0_probe.py  # access gate
```

## Known gaps / next

- Docker image builds and runs; Compose stack (api/worker/scheduler) verified
  end-to-end including the containerized `202 → worker → 200` flow.
- Structured logging on worker + client; `/v1/meta` upstream-health probe pending.
- Golden-fixture drift tests per sport still pending.
- Postgres migration only if a second worker/instance is introduced.
