#!/bin/sh
# Nightly logical backup: pg_dump custom format, optional upload, local retention.
#   PGHOST PGUSER PGPASSWORD PGDATABASE   connection (PGPASSWORD may come from PGPASSWORD_FILE)
#   BACKUP_DIR (default /backups)  BACKUP_KEEP_DAYS (default 14)
#   BACKUP_UPLOAD_CMD   optional, e.g. 'aws s3 cp "$1" s3://bucket/marketedge/' or 'az storage blob upload -f "$1" ...'
set -eu
[ -n "${PGPASSWORD_FILE:-}" ] && PGPASSWORD="$(cat "$PGPASSWORD_FILE")" && export PGPASSWORD
DIR="${BACKUP_DIR:-/backups}"; mkdir -p "$DIR"
FILE="$DIR/marketedge-$(date -u +%Y%m%dT%H%M%SZ).dump"
pg_dump --format=custom --compress=6 --no-owner --file="$FILE.partial"
pg_restore --list "$FILE.partial" > /dev/null   # fails if the archive is unreadable
mv "$FILE.partial" "$FILE"
echo "backup ok: $FILE ($(du -h "$FILE" | cut -f1))"
if [ -n "${BACKUP_UPLOAD_CMD:-}" ]; then sh -c "$BACKUP_UPLOAD_CMD" _ "$FILE"; fi
find "$DIR" -name 'marketedge-*.dump' -mtime +"${BACKUP_KEEP_DAYS:-14}" -delete
