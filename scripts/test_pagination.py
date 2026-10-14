#!/usr/bin/env python3
"""Network-free checks for tournament-index pagination and multi-day sync.

Run: python scripts/test_pagination.py
"""
from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ss_pag_"))
os.environ.setdefault("SPORT_SCRAPER_DATA", str(_TMP))
os.environ.setdefault("SPORT_SCRAPER_DB", str(_TMP / "pag.db"))

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sport_scraper.sofascore.pipeline import Pipeline  # noqa: E402


def _item(tid: int) -> dict:
    return {"tournament": {"uniqueTournament": {"id": tid}}}


class FakeEP:
    """Serves scripted index pages and records which pages were requested."""

    def __init__(self, pages: list[dict]):
        self.pages = pages
        self.calls: list[int] = []

    def tournament_index(self, sport: str, d, page: int = 1):
        self.calls.append(page)
        return self.pages[page - 1]


def test_paginates_and_dedupes():
    pages = [
        {"scheduled": [_item(1), _item(2)], "hasNextPage": True},
        {"scheduled": [_item(2), _item(3)], "hasNextPage": True},  # 2 repeats
        {"scheduled": [_item(3), _item(4)], "hasNextPage": False},
    ]
    p = Pipeline()
    p.ep = FakeEP(pages)  # type: ignore[assignment]
    ids = p._tournament_ids("football", "2026-10-13")
    assert ids == [1, 2, 3, 4], ids
    assert p.ep.calls == [1, 2, 3], p.ep.calls
    print("pagination + dedupe ok:", ids)
    p.close()


def test_stops_on_empty_page():
    pages = [{"scheduled": [], "hasNextPage": True}]  # defensive: empty but "more"
    p = Pipeline()
    p.ep = FakeEP(pages)  # type: ignore[assignment]
    assert p._tournament_ids("tennis", "2026-10-13") == []
    assert p.ep.calls == [1], p.ep.calls
    print("empty-page guard ok")
    p.close()


def test_respects_page_cap():
    from sport_scraper.sofascore.pipeline import MAX_INDEX_PAGES

    pages = [{"scheduled": [_item(i)], "hasNextPage": True} for i in range(1, 200)]
    p = Pipeline()
    p.ep = FakeEP(pages)  # type: ignore[assignment]
    ids = p._tournament_ids("football", "2026-10-13")
    assert len(ids) == MAX_INDEX_PAGES, len(ids)
    assert p.ep.calls[-1] == MAX_INDEX_PAGES, p.ep.calls[-1]
    print(f"page cap ok ({MAX_INDEX_PAGES} pages)")
    p.close()


def test_run_days_spans_range():
    p = Pipeline()
    seen: list[str] = []

    def fake_discover(sport, d, with_stats=False, with_odds=False):
        seen.append(d)
        return 1

    p.discover_sport = fake_discover  # type: ignore[assignment]
    res = p.run_days(days=3, start=date(2026, 10, 14), sports=("football",))
    assert res["total_events"] == 3, res
    assert sorted(res["by_day"]) == [
        "2026-10-14",
        "2026-10-15",
        "2026-10-16",
    ], res
    assert set(seen) == {"2026-10-14", "2026-10-15", "2026-10-16"}, seen
    print("multi-day range ok:", list(res["by_day"]))
    p.close()


def main() -> int:
    test_paginates_and_dedupes()
    test_stops_on_empty_page()
    test_respects_page_cap()
    test_run_days_spans_range()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
