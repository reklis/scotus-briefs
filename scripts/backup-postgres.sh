#!/bin/sh
set -eu
: "${RAGCHEW_DATABASE_DSN:?required}"
: "${BACKUP_DIR:=/backup}"
mkdir -p "$BACKUP_DIR"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
umask 077
# Preserve ACLs for the private/public boundary while omitting environment-specific ownership.
pg_dump --format=custom --no-owner "$RAGCHEW_DATABASE_DSN" \
  > "$BACKUP_DIR/ragchew-$stamp.dump"
find "$BACKUP_DIR" -type f -name 'ragchew-*.dump' -mtime +14 -delete
