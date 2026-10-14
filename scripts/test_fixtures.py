#!/usr/bin/env python3
"""Golden-fixture drift test.

Replays the normalizer over each captured upstream fixture
(``tests/fixtures/*.json``) and asserts the output still matches the stored
expectation. A diff means either upstream changed shape or a parser changed.

Run: python scripts/test_fixtures.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from sport_scraper.sofascore.normalize import (  # noqa: E402
    normalize_event,
    normalize_statistics,
)

FIXTURE_DIR = ROOT / "tests" / "fixtures"


def check(path: Path) -> None:
    fx = json.loads(path.read_text())
    kind = fx.get("kind")
    if kind == "event":
        got = normalize_event(fx["raw"], fx.get("sport"))
    elif kind == "statistics":
        got = normalize_statistics(fx["raw"])
    elif kind == "odds":
        got = fx["raw"]
    else:
        got = fx["raw"]
    if got != fx["normalized"]:
        raise AssertionError(f"{path.name}: normalizer output drifted")


def main() -> int:
    paths = sorted(FIXTURE_DIR.glob("*.json"))
    if not paths:
        print(f"no fixtures in {FIXTURE_DIR}", file=sys.stderr)
        return 1
    for p in paths:
        check(p)
        print(f"  ok {p.name}")
    print(f"ALL OK ({len(paths)} fixtures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
