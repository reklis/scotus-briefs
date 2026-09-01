#!/bin/sh
set -eu
: "${RAGCHEW_DATABASE_DSN:?required}"
: "${1:?usage: restore-postgres.sh backup.dump}"
pg_restore --clean --if-exists --no-owner \
  --dbname "$RAGCHEW_DATABASE_DSN" "$1"
