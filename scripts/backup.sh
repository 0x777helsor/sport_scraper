#!/usr/bin/env bash
# Daily SQLite backup using the online-backup API (safe while writers run).
#
# Usage:
#   scripts/backup.sh [DB_PATH] [BACKUP_DIR] [KEEP_DAYS]
#
# Restore:
#   scripts/restore.sh <backup.db.gz> [DB_PATH]
#
# The system `sqlite3` CLI is not required: the Python stdlib backup API is used.
set -euo pipefail

DB_PATH="${1:-${SPORT_SCRAPER_DB:-data/sport_scraper.db}}"
BACKUP_DIR="${2:-${SPORT_SCRAPER_BACKUP_DIR:-data/backup}}"
KEEP_DAYS="${3:-7}"

# Prefer the project venv, fall back to any python3.
PY="python3"
if [ -x ".venv/bin/python3" ]; then PY=".venv/bin/python3"; fi

mkdir -p "$BACKUP_DIR"

"$PY" - "$DB_PATH" "$BACKUP_DIR" "$KEEP_DAYS" <<'PYEOF'
import gzip
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

db_path, backup_dir, keep_days = sys.argv[1], sys.argv[2], int(sys.argv[3])
stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
dest = Path(backup_dir) / f"sport_scraper-{stamp}.db"

src = sqlite3.connect(db_path)
bck = sqlite3.connect(str(dest))
with bck:
    src.backup(bck)  # transactional, safe against concurrent writers
bck.close()
src.close()

with open(dest, "rb") as fi, gzip.open(str(dest) + ".gz", "wb") as fo:
    shutil.copyfileobj(fi, fo)
dest.unlink()
print(f"backup written: {dest}.gz")

# Prune old backups.
cutoff = time.time() - keep_days * 86400
for f in Path(backup_dir).glob("sport_scraper-*.db.gz"):
    if f.stat().st_mtime < cutoff:
        f.unlink()
        print(f"pruned: {f}")
PYEOF

echo "kept:"
ls -1 "$BACKUP_DIR" | tail -5
