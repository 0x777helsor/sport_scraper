# Operations runbook

For the worker-warmed SofaScore provider. Design detail lives in
[`PLAN.md`](PLAN.md) §16; phase status in [`ROADMAP.md`](ROADMAP.md).

The guiding invariant: **only the worker/scheduler talks to upstream.** The API
reads SQLite and enqueues jobs. So "is upstream healthy?" is answered from the
raw-payload ledger, never by the API calling out.

---

## 1. Health and metrics

| Probe | What it tells you |
|---|---|
| `GET /healthz` (via Caddy) | Proxy liveness only; no DB |
| `GET /v1/health` | API process is up |
| `GET /v1/meta` | Store counts, queue depth, **upstream health**, auth flag |
| `GET /v1/upstream` | Upstream status (`ok` / `degraded` / `unknown`), last request, last error |
| `GET /v1/jobs/{id}` | One job's lifecycle (`pending`/`running`/`done`/`error`) |

`/v1/meta` → `queue` gives counts per status; `done`/`error` grow, `pending`
should drain. `upstream.status` flips to `degraded` when recent traffic shows
non-2xx or the most recent request failed.

Watch:

- **Consecutive upstream 403s** — circuit breaker threshold (`SofaConfig.breaker_threshold`, default 5, 60s open).
- **Queue depth and oldest pending job** — backlog means upstream is slow/blocked.
- **Failed job rate** — `queue.error`.
- **Upstream 429 frequency** — pacing too aggressive.
- **Quota rejections** — `429`s per client (`scripts/keys.py usage`).

---

## 2. Common incidents

| Symptom | Likely cause | Action |
|---|---|---|
| All requests suddenly `403` | egress IP blocked / WAF change | Circuit breaker opens automatically. Re-run `scripts/phase0_probe.py`. Try another egress (proxy). Confirm `curl_cffi` impersonation + headers. |
| `429` storms | pacing too tight / retry loop | Raise `SofaConfig.min_interval` or lower `FETCH_CONCURRENCY`; confirm backoff honours `Retry-After`. |
| Queue backing up | upstream slow/blocked | Check `/v1/upstream`; reduce enrichment scope; scale worker concurrency cautiously. |
| Stale data | scheduler stalled | Check `scheduler` container logs; confirm the 6h loop is running; run `sync` manually. |
| One league/date missing after a long `sync` | `sync` index crawl was interrupted before that tournament's page | `... cli warm --tournament <id> --date <YYYY-MM-DD> --days <N>` (or `--map-file`); it fetches only the mapped tournaments. |
| Parse failures spiking | upstream shape drift | Inspect `raw_payloads` for the failing URL; update the parser; run `scripts/capture_fixtures.py` to refresh golden fixtures. |
| `202` never turns `done` | worker down | `docker compose ps`; restart `worker`; jobs are leased and reclaimed automatically. |
| `database is locked` | second writer introduced | Enforce the single-writer rule; only one `worker` service. WAL allows many readers. |

---

## 3. Simulating failure modes (Phase 7 gate)

Run these against a scratch DB (`SPORT_SCRAPER_DB=/tmp/opencode/rb.db`) so the
real store is untouched.

**Upstream 403 storm → breaker opens**

```bash
.venv/bin/python3 - <<'PY'
from sport_scraper.sofascore.client import SofaScoreClient
from sport_scraper.config import SofaConfig
import dataclasses
c = SofaScoreClient(log_raw=False)
c.cfg = dataclasses.replace(c.cfg, breaker_threshold=2, breaker_reset=30.0)
class R:            # fake a 403 response
    status_code, text, headers = 403, "{}", {}
c._session = lambda: type("S", (), {"get": lambda self, u: R()})()
for _ in range(2):
    try: c.get("/x")
    except RuntimeError as e: print("403 ->", e)
try: c.get("/x")
except RuntimeError as e: print("breaker:", e)   # circuit open, no new call
PY
```

**Upstream 429 storm → retry with backoff, then success**

```bash
.venv/bin/python3 scripts/test_client.py     # covers retry->success and breaker
```

**Worker crash mid-run → lease reclaim**

A crashed worker leaves a job `running` with a short lease; the next worker
reclaims it instead of it being stuck forever.

```bash
export SPORT_SCRAPER_DB=/tmp/opencode/rb.db
.venv/bin/python3 - <<'PY'
import time
from sport_scraper.store.sqlite import Store
from sport_scraper.store.jobs import Jobs
from sport_scraper.config import DB_PATH
s = Store(DB_PATH); j = Jobs(s)
job = j.enqueue("stats", "1"); print("enqueued", job.id)
a = j.claim_next("owner1", lease_seconds=1); print("owner1 claimed", a.status)
time.sleep(2)                                   # owner1 "crashes"; lease expires
b = j.claim_next("owner2")
print("reclaimed:", b.id == job.id, "attempts:", b.attempts)
s.close()
PY
```

**Backup / restore**

```bash
scripts/backup.sh data/sport_scraper.db /tmp/opencode/rb_backup 7
scripts/restore.sh /tmp/opencode/rb_backup/sport_scraper-*.db.gz /tmp/opencode/rb_restored.db
# compare: SELECT count(*) FROM events;
```

**Upstream health view flips to degraded**

After a forced failure above, with the worker configured to log raw payloads,
`GET /v1/upstream` reports `status: degraded` and the offending `last_error`.

---

## 4. Backup and restore (routine)

```bash
scripts/backup.sh                                   # -> data/backup/*.db.gz (keeps 7 days)
scripts/restore.sh data/backup/sport_scraper-<ts>.db.gz
```

Backups use SQLite's online-backup API, so they are safe while the worker and
API run. Restore into the running DB is transactional; stop writers if you want
a hard cut-over. Do a restore drill monthly.

---

## 5. Key management

```bash
python scripts/keys.py create --client acme --quota 10000   # plaintext shown once
python scripts/keys.py usage                                # per-client counts
python scripts/keys.py revoke --client acme                 # immediate (200 -> 403)
```

Keys are stored as `sha256` only. Revoked keys fail closed with `403`; unknown
keys get `401`; over-quota gets `429` + `Retry-After` + `X-RateLimit-*`.

---

## 6. Moving to a VPS

1. Re-run `scripts/phase0_probe.py` **from the VPS IP** — datacenter ranges are
   often blocked and a proxy may be required.
2. Point DNS at the host and enable Caddy HTTPS:
   `SITE_ADDRESS=api.example.com docker compose --profile https up -d`.
3. Keep the same Compose file; only egress/proxy config changes.
4. Confirm ports 80/443 are reachable and certificates issued.
