#!/usr/bin/env bash
# Rebuild the database from empty, exactly as HANDOVER.md documents.
#
#   bash scripts/rebuild.sh
#
# Uses $DATABASE_URL, which Replit sets. Replit's Postgres is hosted, so
# dropdb/createdb are not available — the schema is dropped and recreated in
# place instead, which has the same effect.
#
# Set CSV_DIR if the three source files are not in ./data.
set -euo pipefail

DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi
CSV_DIR="${CSV_DIR:-data}"

SALES="$CSV_DIR/McMc_Partners_20260811_Sales_Transaction_List.csv"
RENEWALS_AUG="$CSV_DIR/McMc_Partners_20260811_Renewals_Pending_Summary.csv"
RENEWALS_APR="$CSV_DIR/McMc_Partners_20260408_Renewals_Pending_Summary.csv"

for f in "$SALES" "$RENEWALS_AUG" "$RENEWALS_APR"; do
    [ -f "$f" ] || { echo "Missing: $f  (set CSV_DIR)"; exit 1; }
done

echo "==> Dropping and recreating the schema"
psql "$DSN" -X -q -v ON_ERROR_STOP=1 \
     -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

echo "==> Migrations 0001 to 0017"
for f in migrations/versions/*.sql; do
    psql "$DSN" -X -q -v ON_ERROR_STOP=1 -f "$f" > /dev/null
    echo "    $(basename "$f")"
done

echo "==> Seed"
python3 -m app.seed.load_seed "$DSN" > /dev/null

echo "==> Sales"
python3 scripts/import_cli.py "$DSN" prepare "$SALES" --user=sam > /dev/null
SALES_BATCH=$(psql "$DSN" -X -qtA -c \
    "SELECT id FROM upload_batch WHERE file_type='sales' AND status='pending' ORDER BY id DESC LIMIT 1")
python3 scripts/import_cli.py "$DSN" accept "$SALES_BATCH" --user=sam --force > /dev/null

echo "==> Renewals, August extract"
python3 scripts/import_cli.py "$DSN" prepare "$RENEWALS_AUG" --user=sam > /dev/null
RENEWALS_BATCH=$(psql "$DSN" -X -qtA -c \
    "SELECT id FROM upload_batch WHERE file_type='renewals' AND status='pending' ORDER BY id DESC LIMIT 1")
DSN="$DSN" BATCH="$RENEWALS_BATCH" python3 - <<'PY' > /dev/null
import datetime as dt, os, sys
import psycopg2
sys.path.insert(0, ".")
from app.importers import accept
conn = psycopg2.connect(os.environ["DSN"])
accept(conn, int(os.environ["BATCH"]), "sam", confirmed_months=[dt.date(2026, 8, 1)])
conn.commit()
PY

echo "==> July 2026, pinned from the April extract"
python3 scripts/set_month_forecast_from_file.py "$DSN" "$RENEWALS_APR" \
    --month=2026-07-01 \
    --reason="April extract: July renewals were still pending"

echo "==> Matching"
python3 scripts/match_report.py "$DSN" --run --user=sam > /dev/null

echo "==> Users"
python3 scripts/create_users.py "$DSN"

echo
echo "Rebuilt. Checking:"
bash scripts/check_state.sh
