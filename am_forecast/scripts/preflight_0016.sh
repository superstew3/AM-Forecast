#!/usr/bin/env bash
# Run BEFORE applying migrations 0016 and 0017.
#
#   bash scripts/preflight_0016.sh
#
# Migration 0016 switches reported income from gross commission and fees to the
# primary associate share. It does that by rewriting generated columns to read
# primary_assoc_amount (sales) and primary_assoc_comm_sum + tax (renewals).
#
# Those columns exist in the 0015 schema and the importer writes them, so the
# migration is meant to apply in place with no re-import. But if this deployment
# imported before that capture was added, the columns will be empty — and the
# migration sets NULL to zero. Every figure in the system would go to zero,
# silently and irreversibly without a restore.
#
# This checks that the data is actually there first.
set -euo pipefail
DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi

psql "$DSN" -X -q <<'SQL'
\pset border 2
SELECT 'sales rows'                              AS measure,
       count(*)::text                            AS value,
       ''                                        AS verdict
FROM sales_transaction WHERE NOT is_excluded
UNION ALL
SELECT 'sales rows with no associate amount',
       count(*) FILTER (WHERE primary_assoc_amount IS NULL
                          OR primary_assoc_amount = 0)::text,
       CASE WHEN count(*) FILTER (WHERE primary_assoc_amount IS NULL
                                    OR primary_assoc_amount = 0) = 0
            THEN 'safe to migrate'
            WHEN count(*) FILTER (WHERE primary_assoc_amount IS NULL
                                    OR primary_assoc_amount = 0)
                 > count(*) * 0.02
            THEN 'STOP: re-import the sales file first'
            ELSE 'check these rows are genuinely zero' END
FROM sales_transaction WHERE NOT is_excluded
UNION ALL
SELECT 'renewal policies',
       count(*)::text, ''
FROM forecast_policy WHERE NOT is_excluded
UNION ALL
SELECT 'renewal policies with no associate commission',
       count(*) FILTER (WHERE primary_assoc_comm_sum IS NULL
                          OR primary_assoc_comm_sum = 0)::text,
       CASE WHEN count(*) FILTER (WHERE primary_assoc_comm_sum IS NULL
                                    OR primary_assoc_comm_sum = 0) = 0
            THEN 'safe to migrate'
            WHEN count(*) FILTER (WHERE primary_assoc_comm_sum IS NULL
                                    OR primary_assoc_comm_sum = 0)
                 > count(*) * 0.02
            THEN 'STOP: re-import the renewals file first'
            ELSE 'check these rows are genuinely zero' END
FROM forecast_policy WHERE NOT is_excluded;
SQL

echo
echo "=== What the change will do to reported income ==="
psql "$DSN" -X -q <<'SQL'
\pset border 2
SELECT to_char(SUM(actual_income), 'FM999,999,990.00')        AS income_now,
       to_char(SUM(primary_assoc_amount), 'FM999,999,990.00') AS income_after,
       to_char(100.0 * (SUM(primary_assoc_amount) - SUM(actual_income))
               / NULLIF(SUM(actual_income), 0), 'FM990.0') || '%' AS change
FROM sales_transaction WHERE NOT is_excluded;
SQL

cat <<'NOTE'

Expect roughly minus 6 to 7 per cent. That is the point of the change: the
brokerage is the primary associate, so the associate amount is what it actually
receives. A figure far from that, or a zero, means the associate columns were
never captured on this deployment and the migration must not be run until the
source files are re-imported.
NOTE
