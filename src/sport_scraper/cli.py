from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date

from sport_scraper.config import DB_PATH
from sport_scraper.sofascore.endpoints import Endpoints
from sport_scraper.sofascore.pipeline import SPORTS, Pipeline
from sport_scraper.store.jobs import Jobs
from sport_scraper.store.sqlite import Store
from sport_scraper.worker.runner import Worker


def cmd_sync(args: argparse.Namespace) -> int:
    start = date.fromisoformat(args.date) if args.date else date.today()
    p = Pipeline()
    try:
        res = p.run_days(days=args.days, start=start)
    finally:
        p.close()
    print(json.dumps(res))
    return 0


def _parse_tournament_ids(values: list[str]) -> list[int]:
    """Parse repeatable and/or comma-separated tournament-id strings."""
    ids: list[int] = []
    for raw in values:
        for piece in str(raw).split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                tid = int(piece)
            except ValueError as exc:
                raise ValueError(f"invalid tournament id {piece!r}") from exc
            if tid <= 0:
                raise ValueError(f"tournament id must be positive: {tid}")
            ids.append(tid)
    return ids


def _load_tournament_map(path: str) -> list[int]:
    """Read tournament ids from a JSON map or list.

    Accepts ``{"label": 18}`` (the ULtFUSE ``SPORT_SCRAPER_TOURNAMENTS`` shape),
    ``{"label": {"id": 18}}``, or a bare ``[18, 53]``. Labels are ignored; only
    the positive integer ids are collected.
    """
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if isinstance(raw, dict):
        entries = list(raw.values())
    elif isinstance(raw, list):
        entries = raw
    else:
        raise ValueError("tournament map must be a JSON object or list")
    ids: list[int] = []
    for val in entries:
        if isinstance(val, dict):
            val = val.get("unique_tournament", val.get("id"))
        if isinstance(val, bool) or not isinstance(val, int) or val <= 0:
            raise ValueError(f"tournament map values must be positive integer ids, got {val!r}")
        ids.append(val)
    return ids


def cmd_warm(args: argparse.Namespace) -> int:
    if args.sport not in SPORTS:
        print(
            f"error: unknown sport {args.sport!r}; expected one of {', '.join(SPORTS)}",
            file=sys.stderr,
        )
        return 2
    try:
        ids = _parse_tournament_ids(args.tournament or [])
        if args.map_file:
            ids.extend(_load_tournament_map(args.map_file))
        start = date.fromisoformat(args.date) if args.date else date.today()
    except (ValueError, OSError) as exc:  # json.JSONDecodeError is a ValueError
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not ids:
        print(
            "error: pass --tournament ID (repeatable/comma-separated) and/or --map-file",
            file=sys.stderr,
        )
        return 2
    p = Pipeline()
    try:
        res = p.warm_tournaments(
            ids,
            days=args.days,
            start=start,
            sport=args.sport,
            with_stats=args.stats,
            with_odds=args.odds,
        )
    finally:
        p.close()
    print(json.dumps(res))
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    w = Worker()
    try:
        if args.once:
            ran = w.run_once()
            print(json.dumps({"ran": ran}))
            return 0
        print(f"worker polling (poll={args.poll}s); Ctrl-C to stop")
        w.run_forever(args.poll)
    except KeyboardInterrupt:
        pass
    finally:
        w.close()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    s = Store(DB_PATH)
    try:
        data = {
            "event": s.get_event(args.ss_id),
            "statistics": s.get_stats(args.ss_id),
            "odds": s.get_odds(args.ss_id),
        }
    finally:
        s.close()
    print(json.dumps({k: (v is not None) for k, v in data.items()}))
    if args.full:
        print(json.dumps(data, indent=2)[:4000])
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    s = Store(DB_PATH)
    try:
        jobs = Jobs(s)
        rows = s.conn.execute(
            "SELECT id,kind,key,status,attempts,updated_at FROM jobs ORDER BY created_at DESC LIMIT ?",
            (args.limit,),
        ).fetchall()
    finally:
        s.close()
    for r in rows:
        print(f"{r[0]}  {r[1]:<8} key={r[2]:<12} {r[3]:<8} tries={r[4]}")
    return 0


def cmd_stats_summary(args: argparse.Namespace) -> int:
    s = Store(DB_PATH)
    try:
        rows = s.conn.execute("SELECT sport, count(*) FROM events GROUP BY sport ORDER BY sport").fetchall()
        counts = {
            "events": s.conn.execute("SELECT count(*) FROM events").fetchone()[0],
            "statistics": s.conn.execute("SELECT count(*) FROM statistics").fetchone()[0],
            "odds": s.conn.execute("SELECT count(*) FROM odds").fetchone()[0],
            "raw_payloads": s.conn.execute("SELECT count(*) FROM raw_payloads").fetchone()[0],
        }
    finally:
        s.close()
    print(json.dumps({"by_sport": dict(rows), **counts}, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sport-scraper", description="SofaScore sport data provider CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("sync", help="ingest fixtures/results for one or more days")
    sp.add_argument("--date", help="start date YYYY-MM-DD (default: today)")
    sp.add_argument("--days", type=int, default=1, help="number of consecutive days (default: 1)")
    sp.set_defaults(func=cmd_sync)

    wm = sub.add_parser(
        "warm",
        help="warm explicit uniqueTournament ids for a date range (targeted sync)",
    )
    wm.add_argument(
        "--sport", default="football", help="sport slug for normalization (default: football)"
    )
    wm.add_argument(
        "--tournament",
        action="append",
        default=[],
        metavar="ID",
        help="uniqueTournament id; repeatable or comma-separated (e.g. 18,53)",
    )
    wm.add_argument(
        "--map-file",
        metavar="PATH",
        help="JSON map of label -> id (accepts the SPORT_SCRAPER_TOURNAMENTS shape)",
    )
    wm.add_argument("--date", help="start date YYYY-MM-DD (default: today)")
    wm.add_argument("--days", type=int, default=1, help="number of consecutive days (default: 1)")
    wm.add_argument("--stats", action="store_true", help="also fetch match statistics per event")
    wm.add_argument("--odds", action="store_true", help="also fetch prematch odds per event")
    wm.set_defaults(func=cmd_warm)

    wp = sub.add_parser("worker", help="run the job-queue worker")
    wp.add_argument("--once", action="store_true", help="process a single job and exit")
    wp.add_argument("--poll", type=float, default=1.0, help="poll interval seconds")
    wp.set_defaults(func=cmd_worker)

    sh = sub.add_parser("show", help="show a cached event by ss_id")
    sh.add_argument("ss_id", help="e.g. ss:15238207")
    sh.add_argument("--full", action="store_true", help="print full payloads")
    sh.set_defaults(func=cmd_show)

    jb = sub.add_parser("jobs", help="list recent jobs")
    jb.add_argument("--limit", type=int, default=20)
    jb.set_defaults(func=cmd_jobs)

    ss = sub.add_parser("summary", help="store counts")
    ss.set_defaults(func=cmd_stats_summary)
    return p


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("SPORT_SCRAPER_LOGLEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
