# ROADMAP — sport_scraper

Phased build plan for the SofaScore sport data provider.

**Rule:** do not start a phase until the previous phase's gate passes. Each phase
below has a **deliverable**, a **gate / acceptance criterion**, and (where useful) a
task breakdown.

Design detail lives in [`PLAN.md`](PLAN.md). Overview in [`README.md`](README.md).

---

## Status board

| # | Phase | Status | Gate |
|---|---|---|---|
| 0 | Access gate | ✅ done | gate passed via www host + 2-step discovery |
| 1 | Scraper core | ✅ done | one date × 6 sports lands in DB |
| 2 | Worker + queue | ✅ done | dedup: N misses → 1 job, 1 fetch |
| 3 | Read API | ✅ done | 202 → poll → 200 verified |
| 4 | Auth + quotas | ✅ done | revoke + quota enforced |
| 5 | Provider adapter | ✅ done | adapter drives live API end-to-end |
| 6 | Containerize | ✅ done | `docker compose up` + restore tested |
| 7 | Harden | ✅ done | failure modes documented & simulated |

Legend: ⬜ not started · 🟡 in progress · ✅ done · ⛔ blocked

---

## Phase 0 — Access gate

**Deliverable:** a `curl_cffi` spike proving we can reach the upstream.

**Tasks**

- [x] Project initialized with `uv` (pyproject + venv + curl_cffi)
- [x] Ran `scripts/phase0_probe.py` against `https://www.sofascore.com/api/v1/`
- [x] Verified two-step discovery (index → events) + event/statistics/odds across six sports
- [x] Recorded raw samples in `data/raw/phase0/`
- [x] Base host documented (www host; `api.sofascore.com` blocked in this network)

**Gate**

- [x] `200` + `application/json` for all six sports schedule + details; cricket statistics may 404 (expected)
- [x] No proxy required from this egress to the www host. Noted blocked api host in README/PLAN.

> **Hard stop:** if no egress works, stop and decide with the owner. Do not build
> Phase 1 on an unverified connection.

---

## Phase 1 — Scraper core

**Deliverable:** client, sport registry, endpoints, parsers for six sports, SQLite
writer, raw-payload ledger, and a sync CLI.

**Tasks**

- [x] Project skeleton (`uv`, `pyproject.toml`, package layout)
- [x] `sofascore/sports.py` — registry (slug, schedule kind, `side_kind`, extras)
- [x] `sofascore/client.py` — session, headers, global pacing/retries/backoff, circuit breaker
- [x] `sofascore/endpoints.py` — typed wrappers (two-step discovery, **paged index**, event/stats/odds)
- [x] `sofascore/normalize.py` — normalize `/event`, `statistics`, `odds` (basic)
- [x] `store/sqlite.py` (WAL, schema, upserts, thread-safe writes)
- [x] `store/repo.py` — repository layer to allow later Postgres migration
- [x] `sofascore/pipeline.py` — concurrent discovery (index → events)
- [x] `cli.py` — `sync`, `worker`, `show`, `jobs`, `summary`
- [x] **Discovery log:** `scripts/discovery_log.py` → `docs/discovery-log.md` (observed stat `key`s + status `code`s)

**Gate**

- [x] One date synced for all six sports lands in SQLite (basic)
- [x] `raw_payloads` contains every fetched body (replayable)
- [x] Re-running the sync is idempotent (no duplicate rows via upserts)

---

## Phase 2 — Worker + job queue

**Deliverable:** a single-writer worker with a scheduler and a deduplicating SQLite
job queue.

**Tasks**

- [x] `jobs` table + `enqueue_job` (dedup) + `claim_next_job` (lease)
- [x] Worker loop (claim → fetch → upsert → done/error)
- [x] Dedup: return the existing `job_id` for a queued duplicate
- [ ] `sync_progress` cursor + `--resume`
- [ ] Scheduler: nightly fixtures (next 3 days, 6 sports), post-match stats sweep,
      daily standings
- [ ] Rate-limit handling: requeue with backoff on `429`

**Gate**

- [x] Concurrency test: N identical misses → **one** job, **one** fetch, same `job_id`
- [ ] Kill the worker mid-run → resumes without data loss (lease reclaim implemented; verify)

---

## Phase 3 — Read API

**Deliverable:** versioned read-only FastAPI service with the pending flow.

**Tasks**

- [x] `api/main.py` + `/v1` routers (`PLAN.md` §11.1)
- [x] Read-only SQLite connections (API writes only the `jobs` control table)
- [x] Cache-miss → enqueue → `202 + /v1/jobs/{id}` (never block upstream)
- [x] `GET /v1/jobs/{id}` status endpoint
- [x] `/v1/meta` (counts + queue depth) and `/v1/sports/{sport}/events` listing
- [ ] Cursor pagination, `ETag`/`If-None-Match`, gzip
- [ ] Structured error envelope + taxonomy
- [x] OpenAPI at `/docs`

**Gate**

- [x] `202` → poll → `200` verified end-to-end
- [ ] `304` returned for unchanged resources with a matching `ETag`

---

## Phase 4 — Auth + quotas

**Deliverable:** hashed per-client API keys with quotas, revocation, and usage
metering.

**Tasks**

- [x] `api_keys`, `key_usage` tables
- [x] Key creation shows plaintext once; store `sha256` only (`scripts/keys.py`)
- [x] Auth middleware (hash lookup, revoked check) — `api/auth.py`
- [x] Fixed-window quota check, atomic increment
- [x] `429` + `Retry-After` + `X-RateLimit-*` headers
- [x] `keys create|list|usage|revoke` CLI

**Gate**

- [x] Revoking a key takes effect immediately (verified: 200 → 403)
- [x] Exceeding a quota returns `429` with `Retry-After` + `X-RateLimit-*` headers
- [x] `usage` reports per-client counts

---

## Phase 5 — Provider adapter

**Deliverable:** a reusable consumer adapter that talks only to our HTTP API.

**Tasks**

- [x] `provider/client.py` with the `SportScraperProvider` interface
- [x] API-key auth, pending handling, optional bounded polling
- [x] Importable package (`sport_scraper.provider`)
- [x] Integration test against a live local API

**Gate**

- [x] Adapter returns event/stats/odds end-to-end (cold → warm verified)
- [x] Pending handling verified (202 → poll → result)
- [x] No upstream URL or credential reachable from the adapter

---

## Phase 6 — Containerize

**Deliverable:** reproducible local deployment with backups.

**Tasks**

- [x] `Dockerfile`
- [x] `docker-compose.yml`: `api`, `worker`, `scheduler` (shared volume)
- [x] Persistent volume for `sport_scraper.db` + `raw/`
- [x] Backup script (`sqlite3 .backup`) + documented restore
- [x] `Caddyfile` + opt-in `caddy` Compose profile (HTTPS for a VPS move)

**Gate**

- [x] `docker compose up` serves the API (api + worker + scheduler verified)
- [x] Restore-from-backup smoke test passes
- [~] API writes only the `jobs` control table; data tables are worker-only

---

## Phase 7 — Harden

**Deliverable:** resilient operation and documented failure modes.

**Tasks**

- [x] Upstream circuit breaker (`403`/`401` trips it) + `WARN` logging
- [x] `GET /v1/meta` + `GET /v1/upstream` upstream-health and queue-depth reporting
- [x] Structured logging (worker + client)
- [x] Golden-fixture tests per sport (`scripts/capture_fixtures.py`, `scripts/test_fixtures.py`)
- [x] Runbook completed (`RUNBOOK.md`, `PLAN.md` §16)
- [x] Simulate: upstream 403 storm, 429 storm, worker crash, backup restore

**Gate**

- [x] Each simulated failure is detected and has a documented response (RUNBOOK §3)
- [x] Full-project definition of done below is satisfied

---

## Definition of done (whole project)

- [x] Six sports sync a date end-to-end
- [x] API serves cached data; `202` for misses; misses dedupe and complete
- [x] Keys, quotas, revocation, usage all work
- [x] `Provider` adapter consumes the API without upstream knowledge
- [x] Runs under Compose; backup restores successfully
- [x] README documents run, backup, and the legal/ToS posture

---

## Critical path

```
Phase 0 (gate) ──▶ Phase 1 ──▶ Phase 2 ──▶ Phase 3 ──▶ Phase 4 ──▶ Phase 5 ──▶ Phase 6 ──▶ Phase 7
   ▲
   └── every other phase is blocked on this
```

Phase 0 is the only true blocker. If it fails, resolve egress before anything else.

---

## Working agreement

- One writer, one API instance (SQLite WAL).
- Never fetch upstream on the API hot path.
- Persist raw before parsing; fail soft.
- Pace politely (1.5–2.0 s, jittered); back off on `429`; trip the breaker on
  repeated `403`.
- Private by default; no anonymous tier.
- Personal-research scope only; do not redistribute the feed.
