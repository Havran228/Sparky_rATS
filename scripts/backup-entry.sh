#!/usr/bin/env bash
# Compose scheduler. Runs backup.sh once per local day at SPARKY_BACKUP_AT
# (default 03:15 Europe/Bratislava). A catch-up run happens if the container
# was down at that minute. SPARKY_BACKUP_ONCE=1 runs a single backup and exits.
set -euo pipefail

BACKUP_BIN=${SPARKY_BACKUP_BIN:-/usr/local/bin/backup.sh}
AT=${SPARKY_BACKUP_AT:-03:15}
POLL=${SPARKY_BACKUP_POLL_SECONDS:-20}
RETRY=${SPARKY_BACKUP_RETRY_SECONDS:-900}
TICKS=${SPARKY_BACKUP_TICKS:-0}
DATA_DIR=${SPARKY_DATA_DIR:-/data}

if [ "${SPARKY_BACKUP_ONCE:-0}" = "1" ]; then
  exec "$BACKUP_BIN"
fi

case $AT in
  [01][0-9]:[0-5][0-9] | 2[0-3]:[0-5][0-9]) ;;
  *)
    echo "SPARKY_BACKUP_AT must be HH:MM in 24-hour time" >&2
    exit 1
    ;;
esac

mkdir -p "$DATA_DIR/backups"
stamp_file="$DATA_DIR/backups/.last_schedule"
ticks=0

while true; do
  today=$(date +%Y-%m-%d)
  hhmm=$(date +%H%M)
  want=$(printf '%s' "$AT" | tr -d ':')
  last=""
  if [ -f "$stamp_file" ]; then
    last=$(tr -d '\r\n' < "$stamp_file")
  fi
  if [ "$((10#$hhmm))" -ge "$((10#$want))" ] && [ "$last" != "$today" ]; then
    if "$BACKUP_BIN"; then
      printf '%s\n' "$today" > "$stamp_file"
    else
      status=$?
      echo "backup exited $status" >&2
      sleep "$RETRY"
    fi
  fi
  ticks=$((ticks + 1))
  if [ "$TICKS" != "0" ] && [ "$ticks" -ge "$TICKS" ]; then
    exit 0
  fi
  sleep "$POLL"
done
