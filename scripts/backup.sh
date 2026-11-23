#!/usr/bin/env bash
# Logical backup of the ResearchLab database (and local file storage, if used).
# Usage: DATABASE_URL=postgresql://... ./scripts/backup.sh [output_dir]
# Note: pg_dump expects a libpq URL; strip the "+psycopg" driver suffix.
set -euo pipefail
OUT="${1:-./backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
URL="${DATABASE_URL/+psycopg/}"
pg_dump --format=custom --no-owner --no-privileges "$URL" > "$OUT/researchlab-$STAMP.dump"
echo "Database dump: $OUT/researchlab-$STAMP.dump"
if [ "${STORAGE_BACKEND:-local}" = "local" ] && [ -d "${LOCAL_STORAGE_PATH:-./backend/storage_data}" ]; then
  tar -czf "$OUT/researchlab-files-$STAMP.tar.gz" -C "${LOCAL_STORAGE_PATH:-./backend/storage_data}" .
  echo "File archive:  $OUT/researchlab-files-$STAMP.tar.gz"
else
  echo "Files are in Supabase Storage; download the bucket from the Supabase dashboard or with the Supabase CLI."
fi
