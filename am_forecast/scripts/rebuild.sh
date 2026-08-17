#!/usr/bin/env bash
# Rebuild the database from empty, at the current migration level.
#
#   bash scripts/rebuild.sh
#
# Uses $DATABASE_URL, which Replit sets. Replit's Postgres is hosted, so
# dropdb/createdb are unavailable -- the schema is dropped and recreated in
# place instead, which has the same effect.
#
# Source files are found by content, not by name: point CSV_DIR at the directory
# holding them and the detector works out which is which. Hard-coded filenames
# from one export are what made the last dataset change so painful.
#
#   CSV_DIR=fixtures bash scripts/rebuild.sh
set -euo pipefail

DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi
CSV_DIR="${CSV_DIR:-fixtures}"
[ -d "$CSV_DIR" ] || { echo "No such directory: $CSV_DIR  (set CSV_DIR)"; exit 1; }

echo "==> Identifying source files in $CSV_DIR"
mapfile -t FOUND < <(CSV_DIR="$CSV_DIR" python3 - <<'PY'
import os, sys, glob
sys.path.insert(0, ".")
from app.importers import detect
sales, renewals = [], []
for p in sorted(glob.glob(os.path.join(os.environ["CSV_DIR"], "*.csv"))):
    try:
        d = detect(p)
    except Exception:
        continue
    if not d.importable:
        continue
    (sales if d.file_type == "sales" else renewals if d.file_type == "renewals" else []).append(p)
print(sales[0] if sales else "")
print("\n".join(renewals))
PY
)
SALES="${FOUND[0]:-}"
RENEWALS=("${FOUND[@]:1}")
[ -n "$SALES" ] && echo "    sales:    $(basename "$SALES")" || echo "    sales:    (none found)"
for r in "${RENEWALS[@]}"; do echo "    renewals: $(basename "$r")"; done
[ ${#RENEWALS[@]} -gt 0 ] || { echo "No renewals file found. Nothing to build."; exit 1; }

echo "==> Dropping and recreating the schema"
psql "$DSN" -X -q -v ON_ERROR_STOP=1 -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"

echo "==> Migrations"
for f in migrations/versions/*.sql; do
    psql "$DSN" -X -q -v ON_ERROR_STOP=1 -f "$f" > /dev/null
    echo "    $(basename "$f")"
done

echo "==> Seed"
python3 -m app.seed.load_seed "$DSN" > /dev/null

if [ -n "$SALES" ]; then
    echo "==> Sales: $(basename "$SALES")"
    python3 scripts/import_cli.py "$DSN" prepare "$SALES" --user=sam > /dev/null
    B=$(psql "$DSN" -X -qtA -c "SELECT id FROM upload_batch WHERE file_type='sales' AND status='pending' ORDER BY id DESC LIMIT 1")
    python3 scripts/import_cli.py "$DSN" accept "$B" --user=sam --force > /dev/null
fi

# The NEWEST renewals extract only. Loading older ones as well is wrong: a newer
# snapshot supersedes an open month, so bulk-loading the history ends with only
# the last file's months present and everything earlier silently gone. Tested --
# it wiped July. Months earlier than the newest extract are established
# deliberately instead, via establish_*_baseline.sql, because an extract taken
# during or after a month has already lost whatever renewed in it.
echo "==> Renewals, newest extract only"
DSN="$DSN" RENEWALS="$(printf '%s\n' "${RENEWALS[@]}")" python3 - <<'PY'
import datetime as dt, os, sys
import psycopg2, polars as pl
sys.path.insert(0, ".")
from app.importers import prepare, accept
from app.importers.normalise import parse_date

def earliest_expiry(path):
    vals = pl.read_csv(path, infer_schema_length=0)["ExpiryDate"].drop_nulls().to_list()
    parsed = []
    for v in vals:
        try:
            parsed.append(parse_date(v))
        except Exception:
            pass
    return min(parsed) if parsed else dt.date.min

conn = psycopg2.connect(os.environ["DSN"])
paths = sorted(os.environ["RENEWALS"].split("\n"), key=earliest_expiry)
for path in paths[-1:]:
    s = prepare(conn, path, "sam")
    all_months = sorted({mc.forecast_month for mc in s.coverage.months}) if s.coverage else []

    # Only months that began AFTER this file was pulled. A pending-renewals
    # report lists what has not yet transacted, so for the month it was pulled in
    # -- and any month before it -- the file is already missing whatever renewed.
    # Establishing those months from it sets a target short by however much of the
    # month had already gone, and under the current-month freeze that is then
    # locked in permanently.
    #
    # The pull date is inferred the same way the importer infers snapshot
    # recency: from the earliest expiry the file contains, since a pending report
    # cannot hold a renewal that has already happened.
    pulled = min(all_months) if all_months else None
    months = [m for m in all_months if pulled is None or m > pulled]
    withheld = [m for m in all_months if m not in months]

    accept(conn, s.batch_id, "sam", confirmed_months=months)
    conn.commit()
    print(f"    {os.path.basename(path)}: pulled ~{pulled}, established {len(months)} months"
          f" ({months[0]} to {months[-1]})" if months else
          f"    {os.path.basename(path)}: no month postdates this extract")
    if withheld:
        print(f"      withheld (extract does not predate them): "
              f"{', '.join(str(m) for m in withheld)}")
        print(f"      establish these deliberately via establish_*_baseline.sql")
if len(paths) > 1:
    skipped = ", ".join(os.path.basename(p) for p in paths[:-1])
    print(f"    not loaded (older extracts): {skipped}")
    print("    establish earlier months deliberately, not by loading these")
PY

echo "==> Users"
python3 scripts/create_users.py "$DSN"

echo
echo "Rebuilt. Checking:"
bash scripts/check_state.sh
