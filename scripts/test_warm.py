"""Network-free checks for the targeted ``warm`` command.

``sync`` crawls the whole per-day tournament index (~2k tournaments on a busy
football day); ``warm`` fetches only explicitly mapped ``uniqueTournament`` ids
for a date range. These checks exercise the fetch/store path with a scripted
endpoint, plus the CLI argument and map-file parsing. No network is used.

    .venv/bin/python3 scripts/test_warm.py
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ss_warm_"))
os.environ.setdefault("SPORT_SCRAPER_DATA", str(_TMP))
os.environ.setdefault("SPORT_SCRAPER_DB", str(_TMP / "warm.db"))

import sys  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sport_scraper import cli  # noqa: E402
from sport_scraper.cli import _load_tournament_map, _parse_tournament_ids  # noqa: E402
from sport_scraper.sofascore.pipeline import Pipeline  # noqa: E402


def _ev(eid: int, day: str, tid: int = 18) -> dict:
    ts = int(datetime.fromisoformat(day + "T14:00:00+00:00").timestamp())
    return {
        "id": eid,
        "startTimestamp": ts,
        "status": {"type": "finished"},
        "homeTeam": {"id": eid * 10, "name": f"Home {eid}"},
        "awayTeam": {"id": eid * 10 + 1, "name": f"Away {eid}"},
        "tournament": {"uniqueTournament": {"id": tid, "name": "Championship"}},
        "homeScore": {"period1": 1, "period2": 0},
        "awayScore": {"period1": 0, "period2": 0},
    }


class FakeEP:
    """Serves scripted ``tournament_events`` and records every (tid, day) call."""

    def __init__(self, by_tid: dict[int, list[dict]], fail: tuple[int, ...] = ()):
        self.by_tid = by_tid
        self.fail = fail
        self.calls: list[tuple[int, str]] = []

    def tournament_events(self, unique_tournament_id: int, d) -> dict:
        if isinstance(d, date):
            d = d.strftime("%Y-%m-%d")
        self.calls.append((unique_tournament_id, d))
        if unique_tournament_id in self.fail:
            raise RuntimeError("upstream 404")
        return {"events": self.by_tid.get(unique_tournament_id, [])}


def test_warm_targeted_and_deduped():
    p = Pipeline()
    p.ep = FakeEP({18: [_ev(1, "2026-10-10"), _ev(2, "2026-10-10")]})  # type: ignore[assignment]
    res = p.warm_tournaments([18, 18], days=1, start=date(2026, 10, 10))
    assert p.ep.calls == [(18, "2026-10-10")], p.ep.calls
    assert res["tournaments"] == [18], res
    assert res["total_events"] == 2 and res["errors"] == 0, res
    assert res["by_day"]["2026-10-10"]["18"] == 2, res
    assert p.store.get_event("ss:1") is not None
    assert p.store.get_event("ss:2")["sport"] == "football"
    print("targeted warm + dedupe ok:", res["by_day"])
    p.close()


def test_warm_multi_day_range():
    p = Pipeline()
    p.ep = FakeEP({18: [_ev(101, "2026-10-09")]})  # type: ignore[assignment]
    res = p.warm_tournaments([18], days=3, start=date(2026, 10, 9))
    assert res["days"] == 3 and res["total_events"] == 3, res
    assert list(res["by_day"]) == ["2026-10-09", "2026-10-10", "2026-10-11"], res
    assert p.ep.calls == [
        (18, "2026-10-09"),
        (18, "2026-10-10"),
        (18, "2026-10-11"),
    ], p.ep.calls
    print("multi-day warm range ok:", list(res["by_day"]))
    p.close()


def test_warm_error_is_isolated_per_tournament():
    p = Pipeline()
    p.ep = FakeEP({53: [_ev(9, "2026-10-10", tid=53)]}, fail=(18,))  # type: ignore[assignment]
    res = p.warm_tournaments([18, 53], days=1, start=date(2026, 10, 10))
    assert res["by_day"]["2026-10-10"]["18"] == -1, res
    assert res["by_day"]["2026-10-10"]["53"] == 1, res
    assert res["errors"] == 1 and res["total_events"] == 1, res
    assert p.store.get_event("ss:9") is not None
    print("per-tournament error isolation ok:", res["by_day"])
    p.close()


def test_id_and_map_parsing():
    assert _parse_tournament_ids(["18,53", " 7 ", ""]) == [18, 53, 7]
    for bad in (["abc"], ["0"], ["-3"], ["18,abc"]):
        try:
            _parse_tournament_ids(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad!r}")

    with tempfile.TemporaryDirectory(prefix="ss_map_") as tmp:
        path = os.path.join(tmp, "map.json")

        def _write(payload):
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)

        # the ULtFUSE SPORT_SCRAPER_TOURNAMENTS shape
        _write({"soccer_england_championship": 18, "soccer_italy_serie_b": {"id": 53}})
        assert sorted(_load_tournament_map(path)) == [18, 53]

        _write({"soccer_usa_mls": {"unique_tournament": 242}})
        assert _load_tournament_map(path) == [242]

        _write([1, 2, 3])
        assert _load_tournament_map(path) == [1, 2, 3]

        _write({"x": "nope"})
        try:
            _load_tournament_map(path)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError for non-integer map value")
    print("id + map parsing ok")


def test_cli_guards_before_network():
    # Each of these must fail fast (return 2) without constructing a Pipeline.
    assert cli.main(["warm"]) == 2
    assert cli.main(["warm", "--sport", "quidditch", "--tournament", "18"]) == 2
    assert cli.main(["warm", "--tournament", "abc"]) == 2
    assert cli.main(["warm", "--map-file", "/nonexistent/map.json"]) == 2
    print("cli fast-fail guards ok")


def main() -> int:
    test_warm_targeted_and_deduped()
    test_warm_multi_day_range()
    test_warm_error_is_isolated_per_tournament()
    test_id_and_map_parsing()
    test_cli_guards_before_network()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
