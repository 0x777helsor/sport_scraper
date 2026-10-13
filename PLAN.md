# PLAN — SofaScore Sport Data Provider

**Instructive design document: what we are building, how the upstream works, and
how to build it correctly.**

Companion docs: [`README.md`](README.md) (overview) · [`ROADMAP.md`](ROADMAP.md) (build order).

---

## Table of contents

1. [Purpose — what we are building and why](#1-purpose--what-we-are-building-and-why)
2. [The upstream reality](#2-the-upstream-reality)
3. [Sports in scope and how their shapes differ](#3-sports-in-scope-and-how-their-shapes-differ)
4. [Endpoint catalogue](#4-endpoint-catalogue)
5. [Phase 0 — the access gate](#5-phase-0--the-access-gate)
6. [Architecture](#6-architecture)
7. [Data model](#7-data-model)
8. [Normalization rules](#8-normalization-rules)
9. [The scraper core](#9-the-scraper-core)
10. [Worker and job queue](#10-worker-and-job-queue)
11. [Public API contract](#11-public-api-contract)
12. [Auth, quotas, and keys](#12-auth-quotas-and-keys)
13. [The Provider adapter](#13-the-provider-adapter)
14. [Deployment](#14-deployment)
15. [Testing strategy](#15-testing-strategy)
16. [Operations runbook](#16-operations-runbook)
17. [Risk register](#17-risk-register)
18. [Open questions](#18-open-questions)
19. [Sources](#19-sources)

---

## 1. Purpose — what we are building and why

### 1.1 The product in one sentence

A **provider-grade sports data service** that ingests fixtures, results, match
statistics and prematch odds from SofaScore for six sports, normalizes them into a
stable internal model, and serves them to consuming applications through a
versioned HTTP API and a reusable Python adapter — **never exposing the upstream to
consumers**.

### 1.2 Why not just scrape on demand

Scraping SofaScore is fragile, rate-limited, and ban-prone. A naive "fetch on
request" design fails three ways:

1. **Latency** — every client request waits on a 1–2 s upstream call (or several).
2. **Amplification** — N clients asking for the same match trigger N upstream
   fetches, multiplying ban risk.
3. **Coupling** — every consuming app would need to know SofaScore's undocumented,
   shifting internals.

The fix is a **decoupled pipeline**: a background worker owns all upstream traffic
and writes to a local store; the API only reads. This is the entire architectural
thesis. Everything below follows from it.

### 1.3 Core design principles

| Principle | Consequence |
|---|---|
| One writer, one API instance | SQLite in WAL mode; API opens read-only connections |
| Upstream is expensive | Workers cache aggressively; never fetch on the hot path |
| Upstream is undocumented | Store raw payloads; fail soft; version the public API |
| Consumers must be insulated | Provider adapter talks only to our API; upstream credentials stay server-side |
| Multiple apps, independent control | Per-client API keys with independent revocation and quotas |
| One machine, local-first | Docker Compose; VPS is a deferred, optional move |

### 1.4 Scope

**In scope (v1):** fixtures/schedules, final results, team and per-player match
statistics, prematch odds, tournaments and standings, entity profiles
(teams/players), search.

**Out of scope (v1):** live in-play streaming, websockets, player-level spatial
data (heatmaps, shotmaps, rating breakdowns), lineups, incidents/timeline. These
are extensions, not foundations — the model leaves room for them.

---

## 2. The upstream reality

### 2.1 There is no official API

SofaScore's own FAQ states it cannot expose API endpoints because its data is
licensed from third parties (Opta, Perform, others) who control redistribution.
Therefore:

- **No SDK, no docs, no stability guarantee.**
- The endpoints below are the **undocumented internal API** the SofaScore
  web/mobile apps call. Community projects reverse-engineered them. Shapes change
  without notice.

### 2.2 Base URLs

| Domain | Role |
|---|---|
| `https://api.sofascore.com/api/v1` | **Primary.** 99% of data. *(blocked from this network — see §5.3)* |
| `https://www.sofascore.com/api/v1` | **Working in this environment.** Web proxy that serves most API paths. |
| `api.sofascore.app` | Historical alternate; not relied upon. |

`/api/v2/`, `/api/v3/`, `/mobile/v4/` were tested and are **404/defunct**. Target
`/api/v1` only.

### 2.3 Anti-bot and TLS fingerprinting — the single biggest technical risk

SofaScore fronts its API with a WAF (Cloudflare/Akamai class) that performs
**TLS/JA3 + HTTP/2 fingerprint checks at the connection level**, before headers are
read.

**Verified locally during planning:**

| Attempt | Result |
|---|---|
| `curl` with Chrome UA | `403` |
| `curl` + `X-Requested-With` + `Referer` + Chrome UA | `403` |

The `403` here is **fingerprint rejection**, not necessarily an IP ban. Plain
`curl`/`requests`/`httpx` are trivially identifiable as non-browser traffic.

**The fix:** `curl_cffi`, which impersonates Chrome's TLS/JA3 and HTTP/2
fingerprints at the socket level. Additional required headers: `X-Requested-With`
(SofaScore answers requests lacking it with a `403` challenge) and
`Referer: https://www.sofascore.com/`.

```python
from curl_cffi import requests

resp = requests.get(
    "https://api.sofascore.com/api/v1/sport/football/scheduled-events/2026-10-13",
    impersonate="chrome",                       # Chrome TLS/JA3 + HTTP2 fingerprint
    headers={
        "X-Requested-With": "XMLHttpRequest",   # required or 403
        "Referer": "https://www.sofascore.com/",
        "Origin": "https://www.sofascore.com",
    },
    timeout=20,
)
```

Two **independent** failure causes — do not conflate them:

1. **Fingerprint rejection** — fixed by `curl_cffi`. Very likely our local case.
2. **IP reputation / geo blocking** — some datacenter ranges and regions get `403`
   regardless. Fixed only by a clean (residential/mobile) egress.

Phase 0 exists to determine which case we are in.

### 2.4 Legal and ethical posture

- SofaScore's Terms prohibit automated access and redistribution.
- **Permitted here:** personal research and use by your own consuming apps.
- **Not permitted:** public redistribution, resale, or commercial products built on
  the raw feed.
- The service must be **private by default** (API keys required; no public tier).
- Be polite: paced requests, backoff, no parallel hammering. Responsible scraping
  is both ethical and operationally necessary.

---

## 3. Sports in scope and how their shapes differ

Six sports. The architecture treats them uniformly via a **sport registry**, but
each has quirks the parser must absorb.

| Sport | Slug | Schedule endpoint | Home/away are | Score structure | Notes |
|---|---|---|---|---|---|
| Soccer | `football` | `scheduled-tournaments/{date}/page/1` → `unique-tournament/{id}/scheduled-events/{date}` | teams | 2 halves + normal time | two-step discovery |
| Basketball | `basketball` | `scheduled-tournaments/{date}/page/1` → `unique-tournament/{id}/scheduled-events/{date}` | teams | 4 quarters + OT | two-step discovery |
| American Football | `american-football` | `scheduled-tournaments/{date}/page/1` → `unique-tournament/{id}/scheduled-events/{date}` | teams | 4 quarters + OT | two-step discovery |
| Baseball | `baseball` | `scheduled-tournaments/{date}/page/1` → `unique-tournament/{id}/scheduled-events/{date}` | teams | 9 innings | two-step discovery |
| Cricket | `cricket` | `scheduled-tournaments/{date}/page/1` → `unique-tournament/{id}/scheduled-events/{date}` | teams | innings (runs/wkts/overs) | two-step discovery |
| Tennis | `tennis` | **`scheduled-tournaments/{date}/page/{n}`** | **players** | sets / games / points | **structurally different** |

### 3.1 The tennis special case (the main structural divergence)

- **Schedule is paged and grouped differently:** tennis does **not** use
  `scheduled-events`. Use `/sport/tennis/scheduled-tournaments/{date}/page/{n}` and
  iterate pages until exhausted.
- **Participants are people, not teams:** `homeTeam`/`awayTeam` contain **player**
  objects (global `player.id`). Doubles matches carry partner entries under
  `subTeams`.
- **Score is set-based:** `homeScore`/`awayScore` are sets won; per-set detail and
  tiebreaks need the event detail. There is no "period1/period2" halves model.
- **Alternative momentum source:** `/event/{id}/tennis-power` replaces the football
  `/graph`, and `/event/{id}/point-by-point` exists for rally-level data.

**Design implication:** the registry's `side_kind` is `team` for five sports and
`player` for tennis; the participants table must accept both. Score periods are
stored generically (see §7) so sets, innings, quarters and halves all fit one shape.

### 3.2 Per-sport statistics

All team sports expose `/event/{id}/statistics` with a **shared envelope**:

```json
{ "statistics": [ { "period": "ALL",
    "groups": [ { "groupName": "...",
        "statisticsItems": [
          { "key": "ballPossession", "name": "Ball possession",
            "homeValue": 61, "awayValue": 39,
            "homeTotal": 107, "awayTotal": 56 } ] } ] } ] }
```

- The **stable machine key is `key`** (camelCase). Store `key`, never the localized
  `name`.
- Ratios carry an optional denominator in `homeTotal`/`awayTotal`.
- **Group names and which stats exist differ per sport.** Do **not** hardcode group
  names or a fixed stat list — the model is **long-format key/value**, so new stats
  require no migration.

> **Action item for Phase 1:** capture one real finished match per sport and record
> observed `key` values into a discovery log. This is empirical, not guessable from
> docs.

---

## 4. Endpoint catalogue

All under `https://api.sofascore.com/api/v1`.

### 4.1 Discovery / schedule

| Endpoint | Purpose |
|---|---|
| `/sport/{sport}/scheduled-tournaments/{YYYY-MM-DD}/page/{n}` | Tournament index for a sport/date. Gives uniqueTournament.id, timezoneEventCount (www host). | 
| `/unique-tournament/{id}/scheduled-events/{YYYY-MM-DD}` | Events for that tournament/date (www host). | 
| `/sport/{sport}/scheduled-events/{YYYY-MM-DD}` | (api host) All events for a sport on a date — blocked/varies; do **not** rely on it for this environment. |
| `/sport/tennis/scheduled-tournaments/{date}/page/{n}` | **Tennis schedule (paged)** |
| `/sport/{sport}/categories` | Competitions/countries for a sport |
| `/sport/{sport}/unique-tournaments` | All unique tournaments for a sport |
| `/config/unique-tournaments/{lang}/{sport}` | Tournaments config + `topUniqueTournamentIds` |

### 4.2 Event detail

| Endpoint | Purpose |
|---|---|
| `/event/{event_id}` | Foundation: teams/players, score, status, tournament, season, round, venue, referee, `defaultPeriodCount` |
| `/event/{event_id}/statistics` | Team stats by period (all team sports) |
| `/event/{event_id}/odds/1/all` | Prematch odds (time-series) |
| `/event/{event_id}/graph` | Momentum graph (team sports) |
| `/event/{event_id}/tennis-power` | Momentum (tennis) |
| `/event/{event_id}/point-by-point` | Rally data (tennis) |
| `/event/{event_id}/lineups` | Lineups + per-player stats (**extension, v2**) |
| `/event/{event_id}/incidents` | Timeline: goals/cards/subs (**extension, v2**) |
| `/event/{event_id}/managers` | Manager bios (**extension, v2**) |

### 4.3 Tournament / standings

| Endpoint | Purpose |
|---|---|
| `/unique-tournament/{tid}/seasons` | Seasons for a tournament |
| `/unique-tournament/{tid}/season/{sid}/standings/{total}` (also `/home`, `/away`) | League table |
| `/unique-tournament/{tid}/season/{sid}/events/round/{n}` | Fixtures/results by round |
| `/unique-tournament/{tid}/season/{sid}/events/last/{n}` | Most recent results |
| `/unique-tournament/{tid}/season/{sid}/events/next/{n}` | Upcoming fixtures |

### 4.4 Entities

| Endpoint | Purpose |
|---|---|
| `/team/{team_id}` | Team profile |
| `/team/{team_id}/players` | Squad |
| `/player/{player_id}` | Player profile |
| `/search/all?q=` | Global search (resolve names → IDs) |
| `/config/popular-entities/{locale}` | Popular leagues by region |

### 4.5 Event object — the fields we rely on

| Field | Use |
|---|---|
| `id` | **event_id** (primary key) |
| `startTimestamp` | kickoff (unix seconds) |
| `status.code`, `status.type`, `status.description` | state — **key on `type`** (`notstarted`, `inprogress`, `finished`, `postponed`, `canceled`, `suspended`, `interrupted`); store `code` for reference |
| `tournament.uniqueTournament.id` | tournament_id |
| `tournament.category` | country/region |
| `season.id` | season_id |
| `roundInfo.round` | round |
| `homeTeam`/`awayTeam` (`.id`, `.name`, `.shortName`, `.nameCode`, `.country`) | participants |
| `homeScore`/`awayScore` (`.current`, `.period1`, `.period2`, …) | scores per period |
| `winnerCode` | 1=home, 2=away, 3=draw |
| `venue`, `referee` | context |
| `defaultPeriodCount` | how many periods this sport uses |
| `hasEventPlayerStatistics`, `hasEventPlayerHeatMap` | enrichment capability flags |

> **Note on status:** the numeric `code` set is not authoritatively documented and
> shifts; **branch on `status.type`**, store `code`/`description` verbatim, and
> never infer state from the code alone.

---

## 5. Phase 0 — the access gate

**Nothing else is worth building until this passes.**

### 5.1 Procedure

```bash
cd ~/sport_scraper
uv init --python 3.12          # 3.12/3.13 preferred: curl_cffi wheels are safest there
uv add curl_cffi
uv run python - <<'PY'
from curl_cffi import requests
r = requests.get(
    "https://api.sofascore.com/api/v1/sport/football/scheduled-events/2026-10-13",
    impersonate="chrome",
    headers={"X-Requested-With": "XMLHttpRequest",
             "Referer": "https://www.sofascore.com/"},
    timeout=20,
)
print(r.status_code, r.headers.get("content-type"))
print(r.text[:300])
PY
```

### 5.2 Decision tree

```
200 + application/json  -> GATE PASSED. Proceed to Phase 1.
403 (fingerprint)       -> confirm impersonate="chrome" set and curl_cffi installed;
                           if still 403 with correct fingerprint -> likely IP reputation.
403 (IP reputation)     -> try egress ladder (below). If all fail -> STOP and raise.
429                     -> working access, but throttled; slow pacing solves it.
```

### 5.3 Egress ladder (only if fingerprint-correct requests still 403)

1. **Cloudflare WARP** (`warp-cli`) — free, changes egress IP, toggles easily.
2. **VPN with a free tier** (e.g. Proton VPN free) — network-level IP change.
3. **Free-tier VPS** (e.g. Oracle Always Free) — different IP but **datacenter**,
   often worse.
4. **Paid residential/mobile proxy** — last resort; the only reliable fix,
   recurring cost.

Proxy support is a first-class client option (`--proxy`), so switching egress never
requires code changes.

### 5.4 Gate acceptance

- [ ] A `200` JSON response from at least one endpoint **from this machine**.
- [ ] Response time and `content-type` recorded.
- [ ] If a proxy was needed, the working egress is documented.

---

## 6. Architecture

```
                 +----------------------------------------------+
  consumers ---->|  API (FastAPI)  read-only                    |
  (your apps)    |  auth . quotas . cache/ETag . pagination     |
                 +---------------+------------------------------+
                                 | SELECT (read-only conn)
                                 v
                 +----------------------------------------------+
                 |  Store: SQLite (WAL)                         |
                 |  events . participants . score_periods .     |
                 |  team_stats . player_stats . odds_snapshots .|
                 |  raw_payloads . jobs . api_keys . api_usage  |
                 +---------------+------------------------------+
                                 | UPSERT (single writer)
                 +---------------+------------------------------+
                 |  Worker (single writer)                      |
                 |  . scheduler (cron)                          |
                 |  . job queue poller                          |
                 |  . pipeline (discover -> detail -> stats -> odds) |
                 +---------------+------------------------------+
                                 | curl_cffi (Chrome impersonation)
                                 v
                    SofaScore internal API  (Cloudflare WAF)
```

### 6.1 Components

| Component | Responsibility | Concurrency |
|---|---|---|
| **API** (`api/`) | Serve normalized JSON; enforce auth/quotas; enqueue misses; never fetch upstream | many readers |
| **Worker** (`worker/`) | Poll job queue + run scheduler; own upstream traffic; write to DB | exactly one |
| **Store** (`store/`) | SQLite WAL + repository layer (narrow interface, Postgres-swappable) | 1 writer / N readers |
| **Provider adapter** (`provider/`) | Consumer SDK; speaks only to our HTTP API | client-side |

### 6.2 Why SQLite is correct here (and when it stops being correct)

WAL mode allows one writer concurrent with many readers. With a single writer
process and a single API instance, SQLite is not a compromise — it is the right
tool: zero operational overhead, transactional, fast for read-heavy workloads.

**Trigger to migrate to Postgres:** a second writer process, a second API instance,
or sustained write contention. The repository layer (§7.4) confines the swap to one
module.

---

## 7. Data model

### 7.1 Design goals

1. **Sport-agnostic** — one schema for half-based and set/innings-based sports.
2. **Idempotent** — every write is an upsert keyed on stable upstream IDs.
3. **Long-format for stats** — never wide columns; new upstream stats need no
   migration.
4. **Raw-first** — every fetched payload is persisted before/alongside parsing.
5. **Namespaced public IDs** — internal IDs are integers; the API exposes `ss:<id>`.

### 7.2 Schema (migration `0001_init`)

```sql
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE sports (
  slug        TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  side_kind   TEXT NOT NULL CHECK (side_kind IN ('team','player'))
);

CREATE TABLE tournaments (
  tournament_id  INTEGER PRIMARY KEY,
  sport          TEXT NOT NULL REFERENCES sports(slug),
  name           TEXT, slug TEXT, country TEXT, category TEXT,
  updated_at     INTEGER
);

CREATE TABLE seasons (
  season_id      INTEGER PRIMARY KEY,
  tournament_id  INTEGER REFERENCES tournaments(tournament_id),
  name           TEXT, year TEXT, updated_at INTEGER
);

-- Participants unify teams AND players (tennis)
CREATE TABLE participants (
  participant_id  INTEGER NOT NULL,
  sport           TEXT NOT NULL,
  kind            TEXT NOT NULL CHECK (kind IN ('team','player')),
  name            TEXT, short_name TEXT, name_code TEXT, slug TEXT,
  country         TEXT, is_national INTEGER,
  raw_extra       TEXT,
  updated_at      INTEGER,
  PRIMARY KEY (participant_id, sport)
);

CREATE TABLE events (
  event_id         INTEGER PRIMARY KEY,
  sport            TEXT NOT NULL,
  tournament_id    INTEGER, season_id INTEGER, round INTEGER,
  kickoff_ts        INTEGER NOT NULL,
  status_type      TEXT NOT NULL,
  status_code      INTEGER, status_description TEXT,
  home_id          INTEGER, away_id INTEGER,
  home_score       INTEGER, away_score INTEGER,
  winner_code      INTEGER,
  period_count     INTEGER,
  venue            TEXT, referee TEXT,
  group_name       TEXT,
  fetched_at       INTEGER NOT NULL,
  detail_fetched_at  INTEGER,
  stats_fetched_at   INTEGER
);
CREATE INDEX idx_events_sport_kickoff ON events(sport, kickoff_ts);
CREATE INDEX idx_events_status ON events(status_type);

-- Generic period scores: halves, quarters, innings, sets
CREATE TABLE score_periods (
  event_id   INTEGER NOT NULL REFERENCES events(event_id),
  side       TEXT NOT NULL CHECK (side IN ('home','away')),
  period_key TEXT NOT NULL,
  value      NUMERIC,
  PRIMARY KEY (event_id, side, period_key)
);

CREATE TABLE team_stats (
  event_id  INTEGER NOT NULL,
  side      TEXT NOT NULL,
  period    TEXT NOT NULL,
  stat_key  TEXT NOT NULL,
  value     NUMERIC,
  total     NUMERIC,
  PRIMARY KEY (event_id, side, period, stat_key)
);

CREATE TABLE player_stats (
  event_id    INTEGER NOT NULL,
  participant_id INTEGER NOT NULL,
  side        TEXT NOT NULL,
  period      TEXT NOT NULL DEFAULT 'ALL',
  stat_key    TEXT NOT NULL,
  value       NUMERIC,
  PRIMARY KEY (event_id, participant_id, period, stat_key)
);

CREATE TABLE odds_snapshots (
  event_id    INTEGER NOT NULL,
  captured_at INTEGER NOT NULL,
  market_key  TEXT NOT NULL,
  bookmaker   TEXT,
  home_odds   REAL, draw_odds REAL, away_odds REAL,
  raw_extra   TEXT,
  PRIMARY KEY (event_id, captured_at, market_key, bookmaker)
);

CREATE TABLE standings (
  tournament_id INTEGER, season_id INTEGER, kind TEXT,
  position INTEGER, participant_id INTEGER,
  matches INTEGER, wins INTEGER, draws INTEGER, losses INTEGER,
  scores_for INTEGER, scores_against INTEGER, points INTEGER,
  updated_at INTEGER,
  PRIMARY KEY (tournament_id, season_id, kind, participant_id)
);

CREATE TABLE raw_payloads (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  endpoint   TEXT NOT NULL,
  resource_key TEXT,
  fetched_at INTEGER NOT NULL,
  status     INTEGER NOT NULL,
  body       TEXT
);
CREATE INDEX idx_raw_resource ON raw_payloads(resource_key, fetched_at);

CREATE TABLE sync_progress (
  scope      TEXT PRIMARY KEY,
  state      TEXT NOT NULL,
  cursor     TEXT,
  updated_at INTEGER
);
```

### 7.3 Schema (migration `0002_api`)

```sql
CREATE TABLE api_clients (
  client_id   TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  created_at  INTEGER NOT NULL
);

CREATE TABLE api_keys (
  key_id      TEXT PRIMARY KEY,
  client_id   TEXT NOT NULL REFERENCES api_clients(client_id),
  key_hash    TEXT NOT NULL UNIQUE,
  prefix      TEXT NOT NULL,
  revoked_at  INTEGER,
  created_at  INTEGER NOT NULL
);

CREATE TABLE api_usage (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  key_id      TEXT NOT NULL,
  ts          INTEGER NOT NULL,
  endpoint    TEXT NOT NULL,
  status_code INTEGER NOT NULL
);
CREATE INDEX idx_usage_key_ts ON api_usage(key_id, ts);

CREATE TABLE quota_windows (
  key_id       TEXT NOT NULL,
  window_kind  TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  count        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (key_id, window_kind, window_start)
);

CREATE TABLE jobs (
  job_id     TEXT PRIMARY KEY,
  kind       TEXT NOT NULL,
  key        TEXT NOT NULL,
  status     TEXT NOT NULL,
  priority   INTEGER NOT NULL DEFAULT 0,
  attempts   INTEGER NOT NULL DEFAULT 0,
  enqueued_at INTEGER NOT NULL,
  started_at  INTEGER, finished_at INTEGER,
  error      TEXT,
  UNIQUE (kind, key, status)
);
CREATE INDEX idx_jobs_status ON jobs(status, priority DESC, enqueued_at);
```

### 7.4 Repository layer

All DB access goes through `store/repo.py` exposing intent-level methods
(`get_event`, `upsert_event`, `enqueue_job`, `claim_job`, `bump_quota`, …). The API
and worker never write raw SQL directly. This is the seam that makes a future
Postgres migration a single-module change.

---

## 8. Normalization rules

1. **Missing stat fields mean zero, not null.** Upstream omits zero counters.
   `stats.get(key, 0)`.
2. **Key on `status.type`, not `status.code`.** Codes shift; types are stable. Store
   both.
3. **Use `key`, never localized `name`,** for statistics.
4. **Store ratios with their denominator** (`value` + `total`).
5. **Periods are strings, not fixed columns.** `period1`, `period2`, `set3`,
   `inning7`, `OT`.
6. **Public IDs are namespaced** (`ss:<id>`). Internal tables keep bare integers.
7. **Timestamps are unix seconds (UTC).** Convert to ISO-8601 only at the API
   boundary.
8. **Persist raw before parsing.** Write to `raw_payloads`, then parse; a parse
   failure is a logged warning, never data loss.
9. **Store unknown fields in `raw_extra`** so new upstream fields are captured
   before we model them.
10. **Never denormalize entity bios into event rows** — store once in
    `participants`, join by ID.

---

## 9. The scraper core

### 9.1 Client responsibilities (`sofascore/client.py`)

```python
class SofaClient:
    def __init__(self, impersonate="chrome", proxy=None,
                 min_delay=1.5, max_delay=2.0, timeout=20, max_retries=4): ...

    def get_json(self, path: str) -> dict:
        # 1. jittered pacing between calls (sleep random.uniform(min,max))
        # 2. Session with impersonate=chrome + required headers
        # 3. On 200 -> record raw payload, return parsed JSON
        # 4. On 403 -> warn "possible IP block"; backoff
        # 5. On 429 -> exponential backoff starting 30s, double each retry
        # 6. On 5xx/network -> retry with backoff up to max_retries
```

**Non-negotiable behaviours:**

| Behaviour | Setting |
|---|---|
| Browser impersonation | `impersonate="chrome"` |
| Required header | `X-Requested-With: XMLHttpRequest` |
| Referer/Origin | `https://www.sofascore.com/` |
| Pacing | 1.5–2.0 s between requests, **jittered** |
| 429 handling | exponential backoff, start 30 s, double |
| Retries | bounded, with backoff |
| Circuit breaker | after N consecutive blocking errors, stop and alert |
| Raw logging | every payload -> `raw_payloads` |
| Proxy | optional `--proxy`, applied to the whole session |

### 9.2 Verified code pattern

```python
from curl_cffi import requests as cffi_requests

_session = cffi_requests.Session(impersonate="chrome", timeout=20)
_session.headers.update({
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
})

resp = _session.get("https://api.sofascore.com/api/v1/event/11352523")
```

### 9.3 Rate budget (planning numbers)

- At ~1.75 s/request: **~2,050 requests/hour** theoretical max.
- A full soccer match enrichment (detail + stats + odds) is **3–5 calls** (v1
  scope).
- **Six sports, one date:** 6 schedule calls + per-event enrichment. A busy date has
  hundreds of events; enrichment is **queued and paced**, never synchronous.

> Design corollary: schedules are cheap and fetched by the scheduler; **per-event
> enrichment is lazy and on-demand** (or backfilled), so we never fan out thousands
> of calls speculatively.

### 9.4 Resume

`sync_progress` stores a per-scope cursor. A rate-limit hit, restart, or crash
resumes from the cursor rather than re-fetching from the start.

---

## 10. Worker and job queue

### 10.1 Jobs

| kind | Trigger | Fetches |
|---|---|---|
| `fixtures` | scheduler (nightly, per sport+date) | `scheduled-events` / tennis paged schedule |
| `event` | miss in API, or backfill | `/event/{id}` |
| `event_stats` | event is finished | `/event/{id}/statistics` |
| `event_odds` | event not started | `/event/{id}/odds/1/all` |
| `standings` | scheduler (daily) | standings endpoint |

### 10.2 Deduplication (the key requirement)

```sql
INSERT INTO jobs (job_id, kind, key, status, enqueued_at, priority)
VALUES (?, ?, ?, 'queued', ?, ?)
ON CONFLICT(kind, key, status) DO NOTHING;
-- then SELECT the row for (kind, key, status='queued') to return its job_id
```

A burst of N identical misses enqueues **one** job; all callers receive the **same
`job_id`** — satisfying "serve cached data immediately; deduplicate missing-data
requests and let workers fetch them."

### 10.3 Worker loop

```
loop:
  job = claim_next_job()
  if not job: sleep(1); continue
  try:
     fetch upstream (paced)
     parse -> upsert
     mark done
  except RateLimited:
     requeue with backoff (attempts+1)
  except Exception as e:
     mark failed; log; raw_payloads already holds the body
```

### 10.4 Scheduler

- **Nightly:** fixtures for the next 3 days, all six sports.
- **Post-match sweep:** re-enqueue finished events lacking stats.
- **Daily:** standings refresh for tracked tournaments.

---

## 11. Public API contract

### 11.1 Endpoints (`/v1`, OpenAPI at `/docs`)

| Endpoint | Returns |
|---|---|
| `GET /v1/meta` | supported sports, providers, schema version, upstream health |
| `GET /v1/sports` | sport registry |
| `GET /v1/fixtures?sport=&date=` | schedules |
| `GET /v1/results?sport=&since=&until=` | finished events |
| `GET /v1/events/{id}` | event detail + period scores |
| `GET /v1/events/{id}/statistics` | team stats (all periods) |
| `GET /v1/events/{id}/odds` | odds snapshot series |
| `GET /v1/tournaments?sport=` | tournaments |
| `GET /v1/tournaments/{id}/standings?season=` | league table |
| `GET /v1/teams/{id}` , `GET /v1/players/{id}` | entity profiles |
| `GET /v1/search?q=` | id resolution |
| `GET /v1/jobs/{job_id}` | pending-job status |
| `GET /healthz` | liveness |

### 11.2 The pending flow (cache-miss contract)

```
GET /v1/events/ss:11352523
  |-- present  -> 200 OK + ETag
  \-- absent   -> enqueue(kind=event, key=ss:11352523)   [deduped]
                  -> 202 Accepted
                     Location: /v1/jobs/{job_id}
                     { "status": "pending", "job_id": "...",
                       "eta_seconds": 15, "resource": "/v1/events/ss:11352523" }

GET /v1/jobs/{job_id}
  |-- queued/running -> 200 { "status": "running" }
  |-- done           -> 200 { "status": "done", "resource": "..." }
  \-- failed         -> 200 { "status": "failed", "error": "..." }
```

**Hard rule:** the API never blocks on the upstream. Bounded, explicit pending
responses only.

### 11.3 Response envelope & cross-cutting

```json
{
  "data": [ ... ],
  "meta": { "next_cursor": "...", "count": 25, "generated_at": "2026-10-13T12:00:00Z" }
}
```

- **Namespaced IDs:** `ss:11352523`.
- **Cursor pagination:** `?limit=&cursor=`.
- **Conditional requests:** `ETag` + `If-None-Match` -> `304`.
- **Compression:** gzip.
- **Error shape:** `{ "error": { "code": "not_found", "message": "..." } }`.
- **Rate-limit headers:** `X-RateLimit-Limit`, `-Remaining`, `-Reset`; `429`
  includes `Retry-After`.

### 11.4 Error taxonomy

| Code | HTTP | Meaning |
|---|---|---|
| `not_found` | 404 | unknown resource |
| `pending` | 202 | enqueued, not ready |
| `validation_error` | 400 | bad params |
| `unauthorized` | 401 | missing/invalid key |
| `forbidden` | 403 | revoked key |
| `quota_exceeded` | 429 | over limit |
| `upstream_unavailable` | 503 | circuit breaker open |

---

## 12. Auth, quotas, and keys

### 12.1 Keys

- Store **only `sha256(plaintext)`**. Show the plaintext exactly once, at creation.
- Key format: `sport_sk_<random>`; keep the `prefix` for identification in logs.
- Per-key revocation (`revoked_at`) is independent per client.

```bash
sport-scraper keys create --client xpredict-web --quota-minute 120 --quota-day 5000
sport-scraper keys list
sport-scraper keys revoke --key-id k_abc123
```

### 12.2 Quotas

- **Fixed-window** counters per key in `quota_windows` (minute and day) — survives
  restarts.
- Check -> increment atomically in one transaction.
- On exceed: `429` + `Retry-After` + rate-limit headers.
- Every request appended to `api_usage`; report via
  `sport-scraper usage --client xpredict-web --since 7d`.

### 12.3 Middleware order

```
request -> extract key -> verify (hash lookup, not revoked)
        -> check quota (atomic) -> handle route -> log usage -> response
```

---

## 13. The Provider adapter

```python
# provider/provider.py
class ProviderError(Exception): ...

class PendingError(ProviderError):
    def __init__(self, job_id: str, eta_seconds: int): ...

class Provider:
    def __init__(self, base_url: str, api_key: str,
                 timeout: float = 10, poll_on_pending: bool = False): ...

    def fixtures(self, sport: str, date: str) -> list[dict]: ...
    def results(self, sport: str, since: str, until: str) -> list[dict]: ...
    def event(self, event_id: str) -> dict: ...
    def statistics(self, event_id: str) -> dict: ...
    def odds(self, event_id: str) -> list[dict]: ...
    def search(self, q: str) -> list[dict]: ...
```

- Speaks **only** to our HTTP API, authenticated with an API key.
- On `202`: raise `PendingError` (default) **or**, with `poll_on_pending=True`, poll
  `/v1/jobs/{id}` respecting `eta_seconds` with a bounded ceiling.
- Upstream URLs, user-agents, proxies and scraping logic **never leave the server**
  — the centralization requirement.

---

## 14. Deployment

### 14.1 Docker Compose (local-first)

```yaml
services:
  api:
    build: .
    command: uvicorn api.app:app --host 0.0.0.0 --port 8000
    volumes: [ "scraper-data:/data:ro" ]       # read-only DB
    environment: [ "SCRAPER_DB=/data/sofascore.db" ]
    ports: [ "8000:8000" ]

  worker:
    build: .
    command: python -m worker.main
    volumes: [ "scraper-data:/data" ]          # sole writer
    environment: [ "SCRAPER_DB=/data/sofascore.db" ]

  caddy:                                        # optional locally; HTTPS on VPS
    image: caddy:2
    volumes: [ "./Caddyfile:/etc/caddy/Caddyfile", "caddy-data:/data" ]
    ports: [ "443:443", "80:80" ]

volumes:
  scraper-data:
  caddy-data:
```

- **Only the worker** mounts `/data` read-write. The API mounts it read-only. This
  structurally enforces the single-writer rule.
- Persistent named volume holds `sofascore.db` and `raw/`.

### 14.2 Backups

- `sqlite3 /data/sofascore.db ".backup /backup/sofascore-$(date +%F).db"` on a
  cron inside the worker.
- Optional: continuous replication with Litestream.
- **Restore procedure documented and tested** — a backup never restored is not a
  backup.

### 14.3 VPS move (deferred)

When/if you move to a VPS:

1. Re-run **Phase 0 from the VPS IP** — datacenter ranges are often blocked; a
   proxy may be required.
2. Enable Caddy automatic HTTPS.
3. Keep the same Compose file; only proxy/egress config changes.

---

## 15. Testing strategy

| Layer | Approach |
|---|---|
| Parsers | **Golden fixtures** — commit real captured JSON per sport; assert normalized output |
| Client | Mock transport; assert headers, pacing, backoff, retry on 429/5xx |
| Dedup | Concurrency test: fire N identical misses, assert one job + one fetch |
| Repo | Round-trip upserts; assert idempotency (double-write = same state) |
| API | Contract tests via TestClient; 200/202/304/429/401 shapes |
| Provider | Integration test against a live local API |
| Backup | Restore-from-backup smoke test |

Golden fixtures are essential because upstream drift is the #1 long-term failure
mode — tests fail loudly when a shape changes.

---

## 16. Operations runbook

**Health:** `GET /healthz` (liveness), `GET /v1/meta` (upstream reachability, last
successful fetch, queue depth).

**Watch metrics:**

- Consecutive upstream `403`s (circuit breaker threshold).
- Job queue depth and age of oldest `queued` job.
- `failed` job rate.
- Upstream `429` frequency (pacing too aggressive).
- Quota rejections per client.

**Common incidents:**

| Symptom | Likely cause | Action |
|---|---|---|
| Sudden all-403 | IP blocked / WAF change | Trip circuit breaker; try egress ladder; verify `curl_cffi` + headers |
| 429 storms | pacing too tight | Increase delays; check for retry loops |
| Queue backing up | upstream slow/blocked | Check egress; lower enrichment scope |
| Stale data | scheduler stalled | Check worker logs; verify cron |
| Parse failures spiking | upstream shape drift | Inspect `raw_payloads`; update parser; add golden fixture |

**Backup/restore:** documented commands, tested monthly.

---

## 17. Risk register

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| 1 | IP/fingerprint `403` blocks access | High | Critical | Phase 0 gate; `curl_cffi`; proxy option; egress ladder |
| 2 | Upstream shape drift breaks parsers | High over time | High | raw_payloads; fail-soft; golden-fixture tests |
| 3 | Rate-limit / ban from aggressive scraping | Medium | High | strict pacing, backoff, circuit breaker, lazy enrichment |
| 4 | Python 3.14 + `curl_cffi` wheel gaps | Medium | Medium | build on 3.12/3.13 |
| 5 | SQLite write contention (if second writer added) | Low now | Medium | single-writer rule; repo layer; pre-planned Postgres path |
| 6 | API key leakage | Low | Medium | hash-at-rest; per-client revocation; no anonymous tier |
| 7 | Legal/ToS exposure | Medium | Medium | private-by-default; no redistribution; documented posture |
| 8 | Tennis schedule paging misses events | Medium | Low | iterate pages until exhausted; assert page count changes |

---

## 18. Open questions

1. **Default sports set:** sync all six by default, or soccer default + others
   opt-in until validated?
2. **Backfill appetite:** forward-only, or a historical backfill (e.g. last season)
   for the six sports?
3. **Odds granularity:** one snapshot per sync, or a repeated schedule (e.g.
   hourly) near kickoff?
4. **Tracked tournaments:** all competitions, or a watchlist for cleaner data and
   less load?
5. **Postgres trigger:** what event forces the migration (second app instance? a
   data volume?)
6. **Proxy:** if Phase 0 fails, which egress rung to try first?

---

## 19. Sources

- curl_cffi — browser TLS/HTTP2 impersonation (lexiforest/curl_cffi README).
- SofaScore public API docs — endpoint catalogue, `403`/WAF notes, tennis
  `scheduled-tournaments` (pseudo-r/public-sofascore-api).
- SofaScore API reference — endpoint shapes and sample responses
  (apdmatos/sofascore-api).
- Field-level extraction reference — statistics keys, long-format schema,
  rate-limit guidance (Tariq-15/TacosScore).
- sportly Sofascore docs — compliance caveat (public reachability != permission).
- Crawlora / Scraperly — 2026 glosses on Cloudflare TLS/JA3 blocking, confirming
  `curl_cffi`.
- SofaScore FAQ — no official API; data licensed from Opta/Perform et al.
- Local verification (this machine): plain `curl` and `curl`+headers both return
  `403`.

> **Reality note:** SofaScore's ToS prohibits automated access and redistribution.
> This project is scoped to private, personal use by your own consuming
> applications. Keep it private: no public tier, no redistribution.

---

## Appendix A — Glossary

| Term | Meaning |
|---|---|
| **Impersonation** | Replicating a browser's TLS/JA3 + HTTP/2 fingerprint so the WAF sees Chrome, not Python |
| **Provider** | Our service, abstracting upstream SofaScore behind a stable contract |
| **Registry** | The per-sport config table driving endpoints, side kinds and parsers |
| **Miss** | An API request for data not yet in the store; triggers a deduped job, returns `202` |
| **Enrichment** | Fetching per-event detail/stats/odds after discovery |
| **WAL** | SQLite write-ahead logging; enables 1 writer + many readers concurrently |
| **Namespaced ID** | `ss:<id>` public identifier, decoupling consumers from upstream IDs |
