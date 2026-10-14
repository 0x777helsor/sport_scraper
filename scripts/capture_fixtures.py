#!/usr/bin/env python3
"""Capture golden fixtures per sport from the raw-payload ledger.

This is a *capture* step, not a test. It reads ``raw_payloads`` (every body the
client has fetched), picks a deterministic sample event for each sport, and
writes ``tests/fixtures/<sport>.json`` as:

    {"sport": ..., "url": ..., "raw": <upstream>, "normalized": <ours>}

``scripts/test_fixtures.py`` then replays the normalizer over ``raw`` and
asserts the output still equals ``normalized`` — catching silent drift in the
upstream schema or our parsers.

Usage: python scripts/capture_fixtures.py
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sport_scraper.config import DB_PATH  # noqa: E402
from sport_scraper.sofascore.normalize import (  # noqa: E402
    normalize_event,
    normalize_statistics,
)
from sport_scraper.sofascore.sports import SPORT_SLUGS  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures"


def _event_id_from_url(url: str) -> int | None:
    # .../event/{id}/statistics , .../event/{id} , .../event/{id}/odds/1/all
    parts = url.split("/event/", 1)
    if len(parts) != 2:
        return None
    head = parts[1].split("/", 1)[0]
    return int(head) if head.isdigit() else None


def main() -> int:
    if not Path(DB_PATH).exists():
        print(f"no database at {DB_PATH}; run a sync first", file=sys.stderr)
        return 1
    con = sqlite3.connect(DB_PATH)

    # ss_id -> (sport, event_id)
    sport_of_event: dict[int, str] = {}
    for ss_id, sport in con.execute("SELECT ss_id, sport FROM events"):
        try:
            sport_of_event[int(ss_id.split(":", 1)[1])] = sport
        except (IndexError, ValueError):
            continue

    # Index the newest schedule body per event, for a stable sample.
    schedule: dict[int, tuple[str, dict]] = {}
    for url, body in con.execute(
        "SELECT url, body FROM raw_payloads WHERE url LIKE '%/scheduled-events/%' ORDER BY id ASC"
    ):
        try:
            data = json.loads(body)
        except ValueError:
            continue
        for e in data.get("events") or []:
            if isinstance(e, dict) and e.get("id") in sport_of_event:
                schedule[e["id"]] = (url, e)

    # statistics / odds samples by event id.
    stats: dict[int, tuple[str, dict]] = {}
    odds: dict[int, tuple[str, dict]] = {}
    for url, body in con.execute(
        "SELECT url, body FROM raw_payloads WHERE url LIKE '%/event/%' ORDER BY id ASC"
    ):
        eid = _event_id_from_url(url)
        if eid not in sport_of_event:
            continue
        try:
            data = json.loads(body)
        except ValueError:
            continue
        if url.endswith("/statistics") and data.get("statistics"):
            stats[eid] = (url, data)
        elif url.endswith("/odds/1/all"):
            odds[eid] = (url, data)
    con.close()

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    written: defaultdict[str, int] = defaultdict(int)

    for sport in SPORT_SLUGS:
        ids = sorted(eid for eid, s in sport_of_event.items() if s == sport)
        # Prefer a covered event; lowest id for determinism.
        picked = next((eid for eid in ids if eid in schedule), None)
        if picked is None:
            print(f"skip {sport}: no schedule sample in raw_payloads", file=sys.stderr)
            continue
        url, raw_event = schedule[picked]
        fixture = {
            "sport": sport,
            "kind": "event",
            "url": url,
            "raw": raw_event,
            "normalized": normalize_event(raw_event, sport),  # type: ignore[arg-type]
        }
        path = FIXTURE_DIR / f"{sport}.json"
        path.write_text(json.dumps(fixture, indent=2, sort_keys=True))
        written["event"] += 1
        print(f"wrote {path.relative_to(Path.cwd())} (event {picked})")

    # One statistics + one odds fixture, where available.
    for sport in SPORT_SLUGS:
        for table, kind in ((stats, "statistics"), (odds, "odds")):
            eid = next(
                (e for e in sorted(table) if sport_of_event.get(e) == sport), None
            )
            if eid is None:
                continue
            url, raw = table[eid]
            normalized = (
                normalize_statistics(raw) if kind == "statistics" else raw
            )
            fixture = {
                "sport": sport,
                "kind": kind,
                "url": url,
                "raw": raw,
                "normalized": normalized,
            }
            path = FIXTURE_DIR / f"{sport}-{kind}.json"
            path.write_text(json.dumps(fixture, indent=2, sort_keys=True))
            written[kind] += 1
            print(f"wrote {path.relative_to(Path.cwd())} (event {eid})")

    if not written:
        print("no fixtures written", file=sys.stderr)
        return 1
    print(f"done: {dict(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
