#!/usr/bin/env bash
# Restore a gzipped SQLite backup produced by scripts/backup.sh.
#
# Usage:
#   scripts/restore.sh <backup.db.gz> [DB_PATH]
#
# Stop the API/worker before restoring, or rely on SQLite's online restore.
set -euo pipefail

BACKUP="${1:?usage: restore.sh <backup.db.gz> [DB_PATH]}"
DB_PATH="${2:-${SPORT_SCRAPER_DB:-data/sport_scraper.db}}"

PY="python3"
if [ -x ".venv/bin/python3" ]; then PY=".venv/bin/python3"; fi

"$PY" - "$BACKUP" "$DB_PATH" <<'PYEOF'
import gzip
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

backup, db_path = sys.argv[1], sys.argv[2]

tmp = Path(tempfile.mkstemp(suffix=".db")[1])
try:
    with gzip.open(backup, "rb") as fi, open(tmp, "wb") as fo:
        shutil.copyfileobj(fi, fo)
    src = sqlite3.connect(str(tmp))
    dst = sqlite3.connect(db_path)
    with dst:
        src.backup(dst)  # online restore into the live DB
    dst.close()
    src.close()
    print(f"restored {backup} -> {db_path}")
finally:
    tmp.unlink(missing_ok=True)
PYEOF
