# Progress

_Last updated: 2026-10-14_

## One-liner
Worker-warmed, read-only SofaScore provider for six sports: upstream scraping
lives entirely in a background worker; the FastAPI service only reads the local
SQLite store and queues misses as jobs.

## Phase status

| Phase | Status | Evidence |
|---|---|---|
| 0 — Access gate | ✅ passed | `scripts/phase0_probe.py` exits 0 via `www.sofascore.com/api/v1` |
| 1 — Scraper core | ✅ complete | two-step discovery, concurrent fetch, hardened client, discovery log |
| 2 — Worker + queue | ✅ | dedup queue + lease-based worker; `202` flow verified |
| 3 — Read API | ✅ | `/v1/health`, `/v1/meta`, `/v1/upstream`, per-sport events, event/stats/odds, jobs |
| 4 — Auth + quotas | ✅ | sha256 keys, revoke, quota + `X-RateLimit-*` (`401`/`403`/`429` verified) |
| 5 — Provider adapter | ✅ | `SportScraperProvider` drives a live API cold→warm |
| 6 — Containerize | ✅ | Dockerfile + compose (api/worker/scheduler/caddy) + backup/restore |
| 7 — Harden | ✅ | runbook, upstream health, golden fixtures, breaker, structured logs |

## Verified behaviours

- Full six-sport sync for one date: **~81s, ~2,700 events** (8 concurrent
  fetchers, single writer).
- Store: 2,327 events / 227 statistics / 254 odds / ~3,588 raw payloads.
- `202 → worker → 200`: verified over HTTP and through the provider adapter.
- Job dedup: N identical misses ⇒ **one** job, **one** upstream fetch.
- Quota: 4th request over a quota of 3 returns **429**; missing key **401**.
- Backup/restore round-trip: event/raw counts identical.
- Client: 429/5xx retried with backoff; 403 storm trips the breaker; 404 not retried.
- Golden fixtures: 10 captured across all six sports; normalizer replay matches.
- Worker crash: expired lease reclaimed by the next worker (attempts increment).
- Upstream health: `/v1/upstream` reports `ok`/`degraded`/`unknown` + last error.

## Test commands

```bash
.venv/bin/python3 scripts/test_jobs.py      # queue dedup + lifecycle
.venv/bin/python3 scripts/test_e2e.py       # 202 -> worker -> 200
.venv/bin/python3 scripts/test_client.py    # retry/backoff/breaker
.venv/bin/python3 scripts/test_fixtures.py  # per-sport parser drift
.venv/bin/python3 scripts/phase0_probe.py   # access gate
```

## Known gaps / next

- All seven phases are complete. Optional future work: Postgres migration if a
  second worker/instance is introduced; additional per-client alerting/notifiers;
  broader fixture coverage (statistics/odds exist for football + basketball only).
- Docker image builds and runs; Compose stack (api/worker/scheduler) verified
  end-to-end including the containerized `202 → worker → 200` flow. Caddy HTTPS
  is wired but not exercised here (no VPS/DNS).
- Upstream health is ledger-derived (worker-owned); the API never calls out.
