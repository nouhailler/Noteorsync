#!/usr/bin/env bash
set -euo pipefail

DATA_DIR="${NOTEOR_DATA_DIR:-./data}"
BACKUP_DIR="${1:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${BACKUP_DIR}/noteor-sync-${STAMP}"

mkdir -p "${OUT_DIR}"

# Online SQLite WAL-safe hot backup via Python sqlite3.Connection.backup
python3 - <<PY
import sqlite3
from pathlib import Path

src_path = Path("${DATA_DIR}") / "noteor_sync.db"
dst_path = Path("${OUT_DIR}") / "noteor_sync.db"
src = sqlite3.connect(str(src_path))
dst = sqlite3.connect(str(dst_path))
with dst:
    src.backup(dst)
src.close()
dst.close()
PY

if [ -d "${DATA_DIR}/blobs" ]; then
  cp -a "${DATA_DIR}/blobs" "${OUT_DIR}/blobs"
fi

echo "Backup completed: ${OUT_DIR}"
