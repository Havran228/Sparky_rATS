#!/usr/bin/env bash
# Restore one backup directory. Refuses to run while the database or Qdrant is busy.
# A logical Qdrant snapshot (points.jsonl) is restored by the API after the backend
# starts. A raw directory snapshot is copied only when the store is unlocked.
# master.key is not part of a backup; put it back yourself before starting the app.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: restore.sh [--yes] <backup-directory-or-stamp>

Without --yes, print the plan and change nothing.
SPARKY_DATA_DIR is the live data directory (default /data).
EOF
}

YES=0
if [ "${1:-}" = "--yes" ]; then
  YES=1
  shift
fi
if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ] || [ $# -ne 1 ]; then
  usage
  exit 2
fi

DATA_DIR=${SPARKY_DATA_DIR:-/data}
ARG=$1
if [ -d "$ARG" ]; then
  SRC=$ARG
else
  SRC="$DATA_DIR/backups/$ARG"
fi

if [ ! -f "$SRC/sparky_rats.db" ] || [ ! -f "$SRC/MANIFEST.txt" ]; then
  echo "backup is missing sparky_rats.db or MANIFEST.txt: $SRC" >&2
  exit 1
fi

if [ -f "$SRC/qdrant/points.jsonl" ]; then
  VECTORS=logical
elif [ -f "$SRC/qdrant/meta.json" ]; then
  VECTORS=raw
else
  VECTORS=none
fi

echo "backup: $SRC"
echo "sqlite: replace $DATA_DIR/sparky_rats.db"
echo "qdrant: $VECTORS"
echo "master.key is not in the backup. Restore credentials/master.key separately."

if [ "$YES" != "1" ]; then
  echo "re-run with --yes to replace the live files"
  exit 0
fi

db_is_busy() {
  local path=$1
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$path" "BEGIN IMMEDIATE; ROLLBACK;" >/dev/null 2>&1
    return
  fi
  python3 - "$path" <<'PY'
import sqlite3
import sys

conn = sqlite3.connect(sys.argv[1], timeout=0.2)
try:
    conn.execute("BEGIN IMMEDIATE")
    conn.rollback()
finally:
    conn.close()
PY
}

if [ -f "$DATA_DIR/sparky_rats.db" ] && ! db_is_busy "$DATA_DIR/sparky_rats.db"; then
  echo "database is busy; stop the backend before restoring" >&2
  exit 1
fi

if [ -e "$DATA_DIR/qdrant/.lock" ] && ! flock -n "$DATA_DIR/qdrant/.lock" true; then
  echo "Qdrant is locked; stop the backend before restoring" >&2
  exit 1
fi

cp "$SRC/sparky_rats.db" "$DATA_DIR/sparky_rats.db.restore"
rm -f "$DATA_DIR/sparky_rats.db-wal" "$DATA_DIR/sparky_rats.db-shm" "$DATA_DIR/sparky_rats.db"
mv "$DATA_DIR/sparky_rats.db.restore" "$DATA_DIR/sparky_rats.db"

if [ "$VECTORS" = "raw" ]; then
  rm -rf "$DATA_DIR/qdrant"
  mkdir -p "$DATA_DIR/qdrant"
  cp -a "$SRC/qdrant/." "$DATA_DIR/qdrant/"
  rm -f "$DATA_DIR/qdrant/.lock"
  echo "replaced the Qdrant directory from the raw snapshot"
elif [ "$VECTORS" = "logical" ]; then
  echo "sqlite is restored. Start the backend, then POST /maintenance/restore"
  echo "with {\"name\":\"$(basename "$SRC")\",\"replace\":true}."
else
  echo "sqlite is restored. This backup has no Qdrant snapshot."
fi
