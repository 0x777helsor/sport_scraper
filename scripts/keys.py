#!/usr/bin/env python3
"""Phase 4 — API-key management (per-client keys, hashed at rest).

Keys are stored as sha256 hashes; the plaintext is shown only once at creation.
This is a small admin CLI over the `api_keys` table.

    python scripts/keys.py create --client acme --quota 10000
    python scripts/keys.py list
    python scripts/keys.py usage
    python scripts/keys.py revoke --client acme
"""
from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sport_scraper.config import DB_PATH  # noqa: E402
from sport_scraper.store.sqlite import Store  # noqa: E402


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def cmd_create(args: argparse.Namespace) -> int:
    store = Store(DB_PATH)
    raw = f"ssk_{secrets.token_urlsafe(32)}"
    kid = f"key_{uuid.uuid4().hex}"
    store.execute(
        "INSERT INTO api_keys (id, client, key_hash, quota, revoked, created_at) VALUES (?,?,?,?,0,?)",
        (kid, args.client, hash_key(raw), args.quota, int(time.time())),
    )
    store.close()
    print(json.dumps({"id": kid, "client": args.client, "quota": args.quota, "api_key": raw}, indent=2))
    print(
        "\nSave this key now - it is not stored in plaintext and cannot be shown again.",
        file=sys.stderr,
    )
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    store = Store(DB_PATH)
    rows = store.conn.execute(
        "SELECT id, client, quota, revoked, created_at FROM api_keys ORDER BY created_at DESC"
    ).fetchall()
    store.close()
    for r in rows:
        print(f"{r[0]}  {r[1]:<20} quota={r[2]:<8} revoked={r[3]}  created={r[4]}")
    if not rows:
        print("(no keys)")
    return 0


def cmd_usage(args: argparse.Namespace) -> int:
    window = int(time.time() // 3600) * 3600
    store = Store(DB_PATH)
    totals = store.conn.execute(
        "SELECT k.client, COUNT(DISTINCT k.id), COALESCE(SUM(u.count), 0) "
        "FROM api_keys k LEFT JOIN key_usage u ON u.key_id = k.id "
        "GROUP BY k.client ORDER BY k.client"
    ).fetchall()
    current = {
        client: n
        for client, n in store.conn.execute(
            "SELECT k.client, COALESCE(SUM(u.count), 0) FROM api_keys k "
            "LEFT JOIN key_usage u ON u.key_id = k.id AND u.window_start=? "
            "GROUP BY k.client",
            (window,),
        ).fetchall()
    }
    store.close()
    print(f"{'client':<20} {'keys':>5} {'total':>10} {'this_window':>12}")
    for client, nkeys, total in totals:
        print(f"{client:<20} {nkeys:>5} {total:>10} {current.get(client, 0):>12}")
    if not totals:
        print("(no keys)")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    store = Store(DB_PATH)
    cur = store.execute("UPDATE api_keys SET revoked=1 WHERE client=?", (args.client,))
    store.close()
    print(json.dumps({"client": args.client, "revoked": cur.rowcount}))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="keys", description="API key admin")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="create a key for a client")
    c.add_argument("--client", required=True)
    c.add_argument("--quota", type=int, default=10000, help="requests per window")
    c.set_defaults(func=cmd_create)

    l = sub.add_parser("list", help="list keys (hashes hidden)")
    l.set_defaults(func=cmd_list)

    u = sub.add_parser("usage", help="per-client request counts")
    u.set_defaults(func=cmd_usage)

    r = sub.add_parser("revoke", help="revoke all keys for a client")
    r.add_argument("--client", required=True)
    r.set_defaults(func=cmd_revoke)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
