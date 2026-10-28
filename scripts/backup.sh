#!/usr/bin/env bash
# Nightly Sparky_rATS backup (ADR-012, ADR-026).
# Copies the SQLite database with the online backup API and, when the backend
# is up, asks it for a logical Qdrant snapshot. Never copies credentials/.
# master.key has to be backed up by hand, somewhere else.
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: backup.sh

Environment:
  SPARKY_DATA_DIR                 data directory (default /data)
  SPARKY_API_BASE                 backend origin, for example http://backend:8000
  SPARKY_API_TOKEN                bearer token; otherwise credentials/api_token
  SPARKY_BACKUP_RETENTION_DAYS    UTC stamp directories to keep, default 14
  SPARKY_BACKUP_DIRECT            1 skips the API and copies files locally

Exit 0 when sqlite and Qdrant were both saved.
Exit 1 on a hard failure.
Exit 3 when the direct copy saved sqlite and left Qdrant alone because it was locked.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

DATA_DIR=${SPARKY_DATA_DIR:-/data}
RETENTION=${SPARKY_BACKUP_RETENTION_DAYS:-14}
API_BASE=${SPARKY_API_BASE:-}
DIRECT=${SPARKY_BACKUP_DIRECT:-0}
umask 077

if ! [[ "$RETENTION" =~ ^[0-9]+$ ]] || [ "$RETENTION" -lt 1 ]; then
  echo "SPARKY_BACKUP_RETENTION_DAYS must be an integer >= 1" >&2
  exit 1
fi

read_token() {
  if [ -n "${SPARKY_API_TOKEN:-}" ]; then
    printf '%s' "$SPARKY_API_TOKEN"
    return
  fi
  local file="$DATA_DIR/credentials/api_token"
  if [ -f "$file" ]; then
    tr -d '\r\n' < "$file"
  fi
}

sqlite_backup() {
  local src=$1 dest=$2
  case $dest in
    *\'*)
      echo "backup path cannot contain a single quote" >&2
      exit 1
      ;;
  esac
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$src" ".backup '$dest'"
    return
  fi
  python3 - "$src" "$dest" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
try:
    source.backup(target)
    target.execute("PRAGMA wal_checkpoint(TRUNCATE)")
finally:
    source.close()
    target.close()
PY
}

copy_qdrant() {
  local src=$1 dest=$2
  mkdir -p "$dest"
  if [ -e "$src/.lock" ] && ! flock -n "$src/.lock" true; then
    rm -rf "$dest"
    return 1
  fi
  if [ -e "$src/.lock" ]; then
    flock -n "$src/.lock" cp -a "$src/." "$dest/"
  else
    cp -a "$src/." "$dest/"
  fi
  rm -f "$dest/.lock"
}

reject_secret_names() {
  local dest=$1
  local hit
  hit=$(find "$dest" \( -name master.key -o -name api_token -o -name mcp_token \) -print)
  if [ -n "$hit" ]; then
    echo "refusing to keep a backup that contains a credential file" >&2
    rm -rf "$dest"
    exit 1
  fi
}

rotate_backups() {
  local root=$1 days=$2 keep=$3
  local cutoff name dir
  cutoff=$(date -u -d "-${days} days" +%Y%m%dT%H%M%SZ)
  for dir in "$root"/*; do
    [ -d "$dir" ] || continue
    name=$(basename "$dir")
    [ "$name" = "$keep" ] && continue
    case $name in
      [0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z) ;;
      *) continue ;;
    esac
    if [[ "$name" < "$cutoff" ]]; then
      rm -rf "$dir"
    fi
  done
}

api_backup() {
  local token cfg out code
  token=$(read_token)
  if [ -z "$token" ]; then
    echo "SPARKY_API_BASE is set and no API token was found" >&2
    return 1
  fi
  case $token in
    *\"*|*\\*)
      echo "API token contains a character the curl config cannot quote" >&2
      return 1
      ;;
  esac
  cfg=$(mktemp)
  out=$(mktemp)
  chmod 600 "$cfg"
  printf 'header = "Authorization: Bearer %s"\n' "$token" > "$cfg"
  if ! code=$(curl -sS --connect-timeout 3 --max-time 300 \
    --config "$cfg" \
    -o "$out" \
    -w '%{http_code}' \
    -X POST \
    -H 'Content-Type: application/json' \
    --data "$(printf '{"retention_days":%s}' "$RETENTION")" \
    "${API_BASE%/}/maintenance/backup"); then
    rm -f "$cfg" "$out"
    return 2
  fi
  rm -f "$cfg"
  if [ "$code" = "200" ] || [ "$code" = "201" ]; then
    cat "$out"
    printf '\n'
    rm -f "$out"
    return 0
  fi
  echo "backup API returned HTTP $code" >&2
  cat "$out" >&2 || true
  rm -f "$out"
  return 1
}

direct_backup() {
  local stamp dest db qdrant_state
  mkdir -p "$DATA_DIR/backups"
  exec 9>"$DATA_DIR/backups/.backup.lock"
  if ! flock -n 9; then
    echo "another backup is running" >&2
    exit 1
  fi
  stamp=$(date -u +%Y%m%dT%H%M%SZ)
  dest="$DATA_DIR/backups/$stamp"
  if [ -e "$dest" ]; then
    echo "backup $stamp already exists" >&2
    exit 1
  fi
  db="$DATA_DIR/sparky_rats.db"
  if [ ! -f "$db" ]; then
    echo "no database at $db" >&2
    exit 1
  fi
  mkdir -p "$dest"
  sqlite_backup "$db" "$dest/sparky_rats.db"
  qdrant_state="absent"
  if [ -d "$DATA_DIR/qdrant" ]; then
    if copy_qdrant "$DATA_DIR/qdrant" "$dest/qdrant"; then
      qdrant_state="directory"
    else
      qdrant_state="skipped-locked"
    fi
  fi
  cat > "$dest/MANIFEST.txt" <<EOF
created_at: $(date -u +%Y-%m-%dT%H:%M:%SZ)
mode: direct
sqlite: sparky_rats.db
qdrant: $qdrant_state
points: n/a
master_key_included: false
master_key: NOT INCLUDED
reminder: Back up $DATA_DIR/credentials/master.key separately. The database backup cannot be decrypted without it.
EOF
  reject_secret_names "$dest"
  rotate_backups "$DATA_DIR/backups" "$RETENTION" "$stamp"
  if [ "$qdrant_state" = "skipped-locked" ]; then
    echo "sqlite backup $stamp is in place; Qdrant was locked so it was not copied" >&2
    echo "POST /maintenance/backup while the backend is up for a logical snapshot." >&2
    exit 3
  fi
  echo "wrote $dest"
}

if [ "$DIRECT" = "1" ]; then
  direct_backup
  exit 0
fi

if [ -n "$API_BASE" ]; then
  set +e
  api_backup
  status=$?
  set -e
  if [ "$status" -eq 0 ]; then
    exit 0
  fi
  if [ "$status" -eq 1 ]; then
    exit 1
  fi
  echo "API unreachable; falling back to a direct backup" >&2
fi

direct_backup
