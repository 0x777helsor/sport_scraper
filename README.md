# sport_scraper — SofaScore Sport Data Provider

A standalone, worker-warmed, read-only HTTP API (plus a reusable Provider
adapter) that serves **fixtures, results, match statistics and prematch odds**
for six sports, scraped from SofaScore's internal API and normalized into a
stable internal model.

> **Core idea:** a background worker owns *all* upstream traffic and writes to a
> local SQLite store; the API only **reads**. Consumers never see SofaScore.

This project is **standalone** — it is not part of `xpredict`/`ULtFuse`.

---

## Sports covered

| Sport | Slug | Participants |
|---|---|---|
| Soccer | `football` | teams |
| Basketball | `basketball` | teams |
| American Football | `american-football` | teams |
| Baseball | `baseball` | teams |
| Cricket | `cricket` | teams |
| Tennis | `tennis` | **players** |

---

## Status

| 0 — Access gate | ✅ passed |
| 1 — Scraper core | ✅ complete |
| 2 — Worker + job queue | ✅ dedup queue + worker verified |
| 3 — Read API | ✅ 202 → poll → 200 verified |
| 4 — Auth + quotas | ✅ keys + quota + usage verified |
| 5 — Provider adapter | ✅ drives live API |
| 6 — Containerize | 🟡 Dockerfile/compose/backup verified |

### Verified from this environment

- Working host: **`https://www.sofascore.com/api/v1`**
  (`api.sofascore.com` returns `403` from this network).
- Discovery is **two-step**:
  1. `/sport/{sport}/scheduled-tournaments/{date}/page/1` → tournament index
  2. `/unique-tournament/{id}/scheduled-events/{date}` → the events
- Event detail: `/event/{id}`, `/event/{id}/statistics`, `/event/{id}/odds/1/all`.
- Full six-sport sync for one date: ~80s, ~2,700 events (concurrent fetches,
  single writer).

## Architecture

```
consumers ──▶ API (FastAPI, read-only)  ──▶ SQLite (WAL)
                auth · quotas · 202         ▲
                                            │ UPSERT (single writer)
                            Worker (job queue, leases)
                                            │ curl_cffi (Chrome TLS/JA3)
                                            ▼
                              SofaScore internal API (www host)
```

### The read contract (worker-warmed)

- **Cache hit** → `200` with the payload.
- **Cache miss** → enqueues a deduplicated job, returns
  `202 Accepted` + `Location: /v1/jobs/{id}`.
- `GET /v1/jobs/{id}` → `pending` | `running` | `done` | `error`.
- N identical misses for the same key produce **one** job → **one** upstream
  fetch.

## Quick start

```bash
cd ~/sport_scraper
uv sync                       # or: uv run python -m ...
uv run python scripts/phase0_probe.py        # access gate (0 = pass)
uv run python -m sport_scraper.cli sync      # ingest today, all sports
uv run python -m sport_scraper.cli worker    # drain the job queue
uv run python -m sport_scraper.cli summary   # store counts
uvicorn sport_scraper.api.main:app --port 8081
```

### CLI

| Command | Purpose |
|---|---|
| `sync [--date YYYY-MM-DD]` | discover fixtures/results for a day (events only) |
| `worker [--once] [--poll S]` | drain the job queue (event/stats/odds fetches) |
| `show ss:ID [--full]` | inspect a cached event |
| `jobs [--limit N]` | list recent jobs |
| `summary` | row counts per table / sport |

### API

| Method | Path | Behaviour |
|---|---|---|
| GET | `/v1/health` | liveness |
| GET | `/v1/meta` | store counts, queue depth, auth state |
| GET | `/v1/sports/{sport}/events` | cached fixtures/results (`?on=YYYY-MM-DD&limit&offset`) |
| GET | `/v1/events/{ss_id}` | event detail — `200` cached / `202` + `Location` on miss |
| GET | `/v1/events/{ss_id}/statistics` | match statistics (same contract) |
| GET | `/v1/events/{ss_id}/odds` | prematch odds (same contract) |
| GET | `/v1/jobs/{job_id}` | job status (`pending`/`running`/`done`/`error`) |

Public IDs are namespaced: `ss:<event_id>`.

### Auth + quotas (optional)

Auth is **off by default** for local dev. Enable it with `SPORT_SCRAPER_AUTH=1`
and issue keys:

```bash
python scripts/keys.py create --client acme --quota 10000   # prints the key once
python scripts/keys.py list
python scripts/keys.py usage                                # per-client request counts
python scripts/keys.py revoke --client acme
```

Keys are stored as `sha256` only. Clients send `X-API-Key: <key>`. Over-quota
requests get `429`; unknown/revoked keys get `401`/`403`.

### Provider adapter

Consuming apps talk to the API through `sport_scraper.provider` — upstream
credentials and scraping never leak:

```python
from sport_scraper.provider import SportScraperProvider

p = SportScraperProvider("http://127.0.0.1:8081", api_key="ssk_...")
event = p.get_event("ss:15238207")          # drives 202 -> poll -> 200 for you
stats = p.get_statistics("ss:15238207")
odds = p.get_odds("ss:15238207")
```

## Layout

```
src/sport_scraper/
  config.py            settings (base URL, paths)
  cli.py               command-line entrypoint
  sofascore/
    client.py          thread-safe curl_cffi client + raw logging
    endpoints.py       typed endpoint wrappers
    normalize.py       upstream -> internal model
    pipeline.py        discovery (index -> events), concurrent fetch
    sports.py          sport registry
  store/
    sqlite.py          WAL store, schema, thread-safe writes
    repo.py            repository layer (Postgres-migration seam)
    jobs.py            dedup job queue with leases
  worker/
    runner.py          job executor loop
  api/
    main.py            read-only FastAPI + 202 flow
    auth.py            API keys + fixed-window quotas
  provider/
    client.py          SportScraperProvider (consumer adapter)
scripts/
  phase0_probe.py      access gate
  keys.py              API key admin
  test_jobs.py         queue dedup/lifecycle test
  test_e2e.py          202 -> worker -> 200 test
```

## Docker

```bash
docker compose up --build          # api on :8080, worker, scheduler (6h)
SPORT_SCRAPER_AUTH=1 docker compose up   # enable API-key auth
```

- `api` — read-only FastAPI (writes only the `jobs` control table on a miss).
- `worker` — drains the job queue, owns all upstream traffic and data writes.
- `scheduler` — re-runs discovery every 6h.
- Data lives on the `sport_data` volume (`/app/data`).

## Backup + restore

```bash
scripts/backup.sh                      # Python sqlite3 online-backup -> data/backup/*.db.gz
scripts/restore.sh data/backup/sport_scraper-YYYY....db.gz
```

Backups use the online-backup API, so they are safe while the worker/API run.

## Legal

SofaScore's ToS prohibits automated access and redistribution. This project is
scoped to **private, personal use** — no public tier, no redistribution. Use at
your own risk.
