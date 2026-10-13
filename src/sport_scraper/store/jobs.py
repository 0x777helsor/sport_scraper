from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any

from sport_scraper.store.sqlite import Store

PENDING = "pending"
RUNNING = "running"
DONE = "done"
ERROR = "error"


@dataclass(slots=True)
class Job:
    id: str
    kind: str
    key: str
    status: str
    error: str | None
    result: dict[str, Any] | None
    created_at: int
    updated_at: int
    attempts: int


def _row_to_job(row: tuple) -> Job:
    return Job(
        id=row[0],
        kind=row[1],
        key=row[2],
        status=row[3],
        error=row[4],
        result=json.loads(row[5]) if row[5] else None,
        created_at=row[6],
        updated_at=row[7],
        attempts=row[8],
    )


class Jobs:
    """Deduplicating job queue backed by the `jobs` table.

    Dedup rule: at most one job per (kind, key) while status == 'pending'.
    Enqueueing an already-pending (or running) job returns the existing row.
    """

    def __init__(self, store: Store):
        self.store = store

    def enqueue(self, kind: str, key: str) -> Job:
        now = int(time.time())
        # App-level dedup inside an IMMEDIATE transaction (single writer).
        # Returns the existing active job if one is pending/running.
        conn = self.store.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            cur = conn.execute(
                "SELECT id,kind,key,status,error,result,created_at,updated_at,attempts FROM jobs "
                "WHERE kind=? AND key=? AND status IN (?,?) ORDER BY created_at ASC LIMIT 1",
                (kind, key, PENDING, RUNNING),
            )
            row = cur.fetchone()
            if row is not None:
                conn.commit()
                return _row_to_job(row)
            job_id = f"job_{uuid.uuid4().hex}"
            conn.execute(
                "INSERT INTO jobs (id, kind, key, status, created_at, updated_at, attempts) "
                "VALUES (?,?,?,?,?,?,0)",
                (job_id, kind, key, PENDING, now, now),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        row = conn.execute(
            "SELECT id,kind,key,status,error,result,created_at,updated_at,attempts FROM jobs WHERE id=?",
            (job_id,),
        ).fetchone()
        assert row is not None
        return _row_to_job(row)

    def get(self, job_id: str) -> Job | None:
        cur = self.store.conn.execute(
            "SELECT id,kind,key,status,error,result,created_at,updated_at,attempts FROM jobs WHERE id=?",
            (job_id,),
        )
        row = cur.fetchone()
        return _row_to_job(row) if row else None

    def get_by_kind_key(self, kind: str, key: str) -> Job | None:
        cur = self.store.conn.execute(
            "SELECT id,kind,key,status,error,result,created_at,updated_at,attempts FROM jobs "
            "WHERE kind=? AND key=? ORDER BY created_at DESC LIMIT 1",
            (kind, key),
        )
        row = cur.fetchone()
        return _row_to_job(row) if row else None

    def claim_next(self, owner: str, lease_seconds: int = 120) -> Job | None:
        now = int(time.time())
        cur = self.store.conn.execute(
            "SELECT id FROM jobs WHERE status=? OR (status=? AND lease_expires_at < ?) "
            "ORDER BY created_at ASC LIMIT 1",
            (PENDING, RUNNING, now),
        )
        row = cur.fetchone()
        if not row:
            return None
        job_id = row[0]
        self.store.conn.execute(
            "UPDATE jobs SET status=?, lease_owner=?, lease_expires_at=?, updated_at=?, attempts=attempts+1 "
            "WHERE id=?",
            (RUNNING, owner, now + lease_seconds, now, job_id),
        )
        self.store.conn.commit()
        return self.get(job_id)

    def complete(self, job_id: str, result: dict[str, Any] | None = None) -> None:
        now = int(time.time())
        self.store.conn.execute(
            "UPDATE jobs SET status=?, result=?, updated_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
            (DONE, json.dumps(result) if result is not None else None, now, job_id),
        )
        self.store.conn.commit()

    def fail(self, job_id: str, error: str) -> None:
        now = int(time.time())
        self.store.conn.execute(
            "UPDATE jobs SET status=?, error=?, updated_at=?, lease_owner=NULL, lease_expires_at=NULL WHERE id=?",
            (ERROR, error, now, job_id),
        )
        self.store.conn.commit()

    def depth(self) -> dict[str, int]:
        """Counts per job status (queue depth)."""
        rows = self.store.conn.execute(
            "SELECT status, count(*) FROM jobs GROUP BY status"
        ).fetchall()
        out = {PENDING: 0, RUNNING: 0, DONE: 0, ERROR: 0}
        for status, n in rows:
            out[status] = n
        return out
