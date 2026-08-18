#!/usr/bin/env bash
# What should the reporting cut-off be, according to the data?
#
#   bash scripts/derive_cutoff.sh
#
# Reads it rather than asking. The prepare step records coverage_start and
# coverage_end on every upload batch, including one still pending, so the period
# a sales export actually covers is already in the database.
#
# The rule, from the project's own convention: the cut-off sits at the end of the
# last GENUINELY COMPLETE month. Setting it forward treats a month still being
# transacted as closed -- every manager then shows a full month's target against
# a part month's income, and reads as catastrophically behind.
#
# So a file covering to the 11th of a month does not make that month complete.
# The last complete month is the one before it.
set -euo pipefail
DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi

echo "=== Sales coverage on record, newest first ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT id, status, left(coalesce(file_name,''),36) AS file_name,
       coverage_start, coverage_end
FROM upload_batch
WHERE file_type = 'sales'
  AND status IN ('pending','accepted')
  AND coverage_end IS NOT NULL
ORDER BY id DESC LIMIT 5;"

echo
echo "=== Derived cut-off ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
WITH latest AS (
    SELECT coverage_end, status, file_name
    FROM upload_batch
    WHERE file_type = 'sales'
      AND status IN ('pending','accepted')
      AND coverage_end IS NOT NULL
    ORDER BY coverage_end DESC, id DESC LIMIT 1
)
SELECT l.coverage_end                                            AS file_covers_to,
       (l.coverage_end = (date_trunc('month', l.coverage_end)
                          + INTERVAL '1 month - 1 day')::date)   AS month_is_complete,
       CASE WHEN l.coverage_end = (date_trunc('month', l.coverage_end)
                                   + INTERVAL '1 month - 1 day')::date
            THEN l.coverage_end
            ELSE (date_trunc('month', l.coverage_end) - INTERVAL '1 day')::date
       END                                                       AS recommended_cut_off,
       (SELECT cut_off_date FROM reporting_settings WHERE id = 1) AS current_cut_off
FROM latest l;"

echo
echo "=== Cross-check against sales rows actually loaded ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT count(*)                       AS sales_rows,
       min(transaction_date)          AS earliest,
       max(transaction_date)          AS latest,
       CASE WHEN count(*) = 0
            THEN 'none loaded -- derive from batch coverage above'
            ELSE 'compare against the derived date' END AS note
FROM sales_transaction WHERE NOT is_excluded;"

cat <<'NOTE'

If month_is_complete is false, the recommended date is the end of the PREVIOUS
month, and that is deliberate.

Sanity check before applying: does the recommended cut-off leave any month with a
budget but no actuals? If so the export does not reach far enough forward, and
the cut-off must go back further still.
NOTE
