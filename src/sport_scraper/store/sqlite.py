from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  ss_id TEXT PRIMARY KEY,
  sport TEXT NOT NULL,
  event_id INTEGER NOT NULL,
  data JSON NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS statistics (
  ss_id TEXT PRIMARY KEY,
  event_id INTEGER NOT NULL,
  data JSON NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS odds (
  ss_id TEXT PRIMARY KEY,
  event_id INTEGER NOT NULL,
  data JSON NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_sport ON events(sport);
CREATE INDEX IF NOT EXISTS idx_events_event_id ON events(event_id);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  key TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','running','done','error')),
  error TEXT,
  result JSON,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL,
  lease_owner TEXT,
  lease_expires_at INTEGER,
  attempts INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
-- At most one *active* job per (kind, key): dedupes concurrent misses.
CREATE UNIQUE INDEX IF NOT EXISTS uq_jobs_active ON jobs(kind, key)
  WHERE status IN ('pending','running');

CREATE TABLE IF NOT EXISTS raw_payloads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  url TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT 'GET',
  status INTEGER,
  content_type TEXT,
  body TEXT NOT NULL,
  fetched_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_raw_payloads_url ON raw_payloads(url);
CREATE INDEX IF NOT EXISTS idx_raw_payloads_fetched_at ON raw_payloads(fetched_at);

CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  client TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  quota INTEGER NOT NULL DEFAULT 10000,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS key_usage (
  key_id TEXT NOT NULL,
  window_start INTEGER NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (key_id, window_start)
);
"""


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._lock = threading.RLock()
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.execute("PRAGMA busy_timeout=5000;")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Thread-safe write helper: serialize writers with an RLock."""
        with self._lock:
            cur = self.conn.execute(sql, params)
            self.conn.commit()
            return cur

    def upsert_event(self, ss_id: str, sport: str, event_id: int, data: dict[str, Any], updated_at: int | None = None) -> None:
        updated_at = updated_at or int(time.time())
        self.execute(
            "INSERT OR REPLACE INTO events (ss_id, sport, event_id, data, updated_at) VALUES (?,?,?,?,?)",
            (ss_id, sport, event_id, json.dumps(data), updated_at),
        )

    def get_event(self, ss_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.execute("SELECT data FROM events WHERE ss_id=?", (ss_id,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def upsert_stats(self, ss_id: str, event_id: int, data: dict[str, Any], updated_at: int | None = None) -> None:
        updated_at = updated_at or int(time.time())
        self.execute(
            "INSERT OR REPLACE INTO statistics (ss_id, event_id, data, updated_at) VALUES (?,?,?,?)",
            (ss_id, event_id, json.dumps(data), updated_at),
        )

    def get_stats(self, ss_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.execute("SELECT data FROM statistics WHERE ss_id=?", (ss_id,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def upsert_odds(self, ss_id: str, event_id: int, data: dict[str, Any], updated_at: int | None = None) -> None:
        updated_at = updated_at or int(time.time())
        self.execute(
            "INSERT OR REPLACE INTO odds (ss_id, event_id, data, updated_at) VALUES (?,?,?,?)",
            (ss_id, event_id, json.dumps(data), updated_at),
        )

    def get_odds(self, ss_id: str) -> dict[str, Any] | None:
        with self._lock:
            cur = self.conn.execute("SELECT data FROM odds WHERE ss_id=?", (ss_id,))
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def insert_raw(self, url: str, method: str, status: int | None, content_type: str | None, body: str, fetched_at: int) -> int:
        cur = self.execute(
            "INSERT INTO raw_payloads (url, method, status, content_type, body, fetched_at) VALUES (?,?,?,?,?,?)",
            (url, method, status, content_type, body, fetched_at),
        )
        return cur.lastrowid

    def list_events(
        self,
        sport: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List cached events, optionally filtered by sport and start-time range."""
        where: list[str] = []
        params: list[Any] = []
        if sport:
            where.append("sport = ?")
            params.append(sport)
        if start_ts is not None:
            where.append("json_extract(data, '$.startTimestamp') >= ?")
            params.append(start_ts)
        if end_ts is not None:
            where.append("json_extract(data, '$.startTimestamp') <= ?")
            params.append(end_ts)
        sql = "SELECT data FROM events"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY json_extract(data, '$.startTimestamp') ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        with self._lock:
            rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "events": self.conn.execute("SELECT count(*) FROM events").fetchone()[0],
                "statistics": self.conn.execute("SELECT count(*) FROM statistics").fetchone()[0],
                "odds": self.conn.execute("SELECT count(*) FROM odds").fetchone()[0],
                "raw_payloads": self.conn.execute("SELECT count(*) FROM raw_payloads").fetchone()[0],
            }

    def upstream_health(self, window_seconds: int = 900) -> dict[str, Any]:
        """Summarize upstream reachability from the raw-payload ledger.

        Purely derived from ``raw_payloads`` (written by the worker/scheduler),
        so the API stays read-only. No upstream call is ever made here.

        Returns ``status``: ``unknown`` (no data), ``ok``, or ``degraded``
        (recent non-2xx traffic or a very recent error).
        """
        now = int(time.time())
        since = now - window_seconds
        with self._lock:
            row = self.conn.execute(
                "SELECT count(*), "
                "       sum(CASE WHEN status BETWEEN 200 AND 299 THEN 1 ELSE 0 END), "
                "       sum(CASE WHEN status IS NULL OR status >= 400 THEN 1 ELSE 0 END) "
                "FROM raw_payloads WHERE fetched_at >= ?",
                (since,),
            ).fetchone()
            total, ok, errors = row[0], row[1] or 0, row[2] or 0
            last = self.conn.execute(
                "SELECT url, status, fetched_at FROM raw_payloads ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_err = self.conn.execute(
                "SELECT url, status, fetched_at FROM raw_payloads "
                "WHERE status IS NULL OR status >= 400 ORDER BY id DESC LIMIT 1"
            ).fetchone()

        def _err(r: tuple | None) -> dict[str, Any] | None:
            if not r:
                return None
            return {"url": r[0], "status": r[1], "at": r[2]}

        last_ok = last is not None and last[1] is not None and 200 <= last[1] < 300
        if last is None:
            status = "unknown"
        elif errors and (not last_ok):
            status = "degraded"
        else:
            status = "ok"
        return {
            "status": status,
            "healthy": None if status == "unknown" else status == "ok",
            "window_seconds": window_seconds,
            "requests": total,
            "ok": ok,
            "errors": errors,
            "error_rate": (errors / total) if total else 0.0,
            "last_request": (
                {"url": last[0], "status": last[1], "at": last[2]} if last else None
            ),
            "last_error": _err(last_err),
        }

    def close(self) -> None:
        self.conn.close()
