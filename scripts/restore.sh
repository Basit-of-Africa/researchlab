#!/usr/bin/env bash
# Restore a dump produced by backup.sh into an EMPTY database.
# Usage: DATABASE_URL=postgresql://... ./scripts/restore.sh backups/researchlab-<stamp>.dump
set -euo pipefail
URL="${DATABASE_URL/+psycopg/}"
psql "$URL" -c "CREATE EXTENSION IF NOT EXISTS vector"
pg_restore --no-owner --no-privileges --dbname "$URL" "$1"
echo "Restored. Run 'alembic upgrade head' in backend/ if the code is newer than the dump."
