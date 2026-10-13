"""Phase 0 access gate probe (canonical).

Confirms the full SofaScore data path from this machine.

Findings that shaped this probe:
  * api.sofascore.com          -> 403 (Varnish edge) from this network. Blocked.
  * www.sofascore.com/api/v1   -> works, and serves most of the API.
  * Discovery is two-step:
      1) /sport/{sport}/scheduled-tournaments/{date}/page/{n}
         -> tournament index with per-timezone counts (NO events)
      2) /unique-tournament/{id}/scheduled-events/{date}
         -> the actual events for that tournament
  * .../scheduled-events/{date} on the sport root 404s on the www host.

Run:  uv run python scripts/phase0_probe.py
Exit code 0 == gate passed (all six sports schedule + at least one detail path).
"""
from __future__ import annotations

import sys
import time
from datetime import date
from pathlib import Path

from curl_cffi import requests

BASE = "https://www.sofascore.com/api/v1"

HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
    "Accept": "application/json, text/plain, */*",
}

SPORTS = [
    "football",
    "basketball",
    "baseball",
    "american-football",
    "tennis",
    "cricket",
]

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "phase0"
TOURNAMENTS_PER_SPORT = 3  # keep the gate quick; full crawl belongs to Phase 1


def get(session: requests.Session, path: str):
    url = path if path.startswith("http") else BASE + path
    t0 = time.perf_counter()
    r = session.get(url)
    return r, time.perf_counter() - t0


def tournament_index(payload: dict) -> list[dict]:
    out = []
    for item in payload.get("scheduled", []) or []:
        if not isinstance(item, dict):
            continue
        t = item.get("tournament")
        ut = (t or {}).get("uniqueTournament") if isinstance(t, dict) else None
        if not (ut and isinstance(ut, dict) and "id" in ut):
            continue
        tec = item.get("timezoneEventCount")
        count = None
        if isinstance(tec, dict):
            # Try common timezone keys; fall back to sum if not present
            count = tec.get("0") or tec.get(0) or (sum(v for v in tec.values() if isinstance(v, int)) or None)
        elif isinstance(tec, int):
            count = tec
        out.append(
            {
                "id": ut["id"],
                "name": ut.get("name"),
                "count": count,
            }
        )
    return out


def main() -> int:
    today = f"{date.today():%Y-%m-%d}"
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = requests.Session(impersonate="chrome", timeout=30)
    session.headers.update(HEADERS)

    print(f"SofaScore Phase 0 gate | base={BASE} | date={today}\n")

    failures: list[str] = []
    details: list[tuple[str, int]] = []  # (sport, finished_event_id)

    for sport in SPORTS:
        idx_path = f"/sport/{sport}/scheduled-tournaments/{today}/page/1"
        try:
            r, dt = get(session, idx_path)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sport:<18} ERROR {type(exc).__name__}: {exc}")
            failures.append(sport)
            continue

        if r.status_code != 200:
            print(f"  {sport:<18} INDEX {r.status_code}  {r.text[:70]}")
            failures.append(sport)
            continue

        (RAW_DIR / f"scheduled-tournaments-{sport}-{today}.json").write_text(r.text)
        tours = tournament_index(r.json())
        total_hint = sum(t["count"] or 0 for t in tours)

        # Fetch events for the first few tournaments and record totals.
        collected: list[dict] = []
        for t in tours[:TOURNAMENTS_PER_SPORT]:
            try:
                er, _ = get(session, f"/unique-tournament/{t['id']}/scheduled-events/{today}")
                if er.status_code == 200:
                    collected.extend(er.json().get("events", []))
            except Exception:  # noqa: BLE001
                pass

        finished = [e for e in collected if e.get("status", {}).get("type") == "finished"]
        print(
            f"  {sport:<18} index=200 ({dt:4.2f}s)  tournaments={len(tours):<4} "
            f"hint_events≈{total_hint:<5} sampled={len(collected):<4} finished={len(finished)}"
        )
        if len(tours) > TOURNAMENTS_PER_SPORT:
            print(f"      (sampled {TOURNAMENTS_PER_SPORT} of {len(tours)} tournaments)")
        if finished:
            details.append((sport, finished[0]["id"]))

    print("\n-- per-event detail path (finished events where available) --")
    for sport, event_id in details:
        for suffix, label in [
            ("", "event"),
            ("/statistics", "statistics"),
            ("/odds/1/all", "odds"),
        ]:
            path = f"/event/{event_id}{suffix}"
            try:
                r, _ = get(session, path)
                ok = r.status_code == 200
                if ok:
                    j = r.json()
                    keys = list(j)[:6] if isinstance(j, dict) else type(j).__name__
                    note = f"keys={keys}"
                    (RAW_DIR / f"{sport}-{event_id}-{label}.json").write_text(r.text)
                else:
                    note = r.text[:70].replace("\n", " ")
                print(f"  {sport:<18} {label:<11} {r.status_code}  {note}")
            except Exception as exc:  # noqa: BLE001
                print(f"  {sport:<18} {label:<11} ERROR {type(exc).__name__}: {exc}")

    print(f"\nraw samples saved to {RAW_DIR}")
    if failures:
        print(f"\nGATE PARTIAL: sports failing index fetch: {failures}")
        return 1
    if not details:
        print("\nGATE PARTIAL: schedule reachable but found no finished events to probe details.")
        return 1
    print("\nGATE PASSED: schedule + event detail reachable via www.sofascore.com/api/v1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
