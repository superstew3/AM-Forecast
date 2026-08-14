#!/usr/bin/env bash
# Run BEFORE applying migrations 0016 and 0017.
#
#   bash scripts/preflight_0016.sh
#
# Corrected. The first version queried forecast_policy.primary_assoc_comm_sum,
# a column migration 0016 CREATES -- so on any database that has not had 0016
# applied, which is precisely every database this script is for, it died with
# "column does not exist" before reporting anything. Every check below tests for
# a column's existence before reading it.
#
# What 0016 does, and why the two sides differ:
#
#   Sales     primary_assoc_amount already exists at 0015 and the importer
#             already writes it. income becomes that column. Migrates cleanly.
#
#   Renewals  primary_assoc_comm_sum and primary_assoc_comm_tax_sum DO NOT
#             exist at 0015. 0016 adds them NOT NULL DEFAULT 0 and makes
#             forecast_contribution a generated column over them, so every
#             existing policy reads zero and the whole renewal forecast, the
#             budget under it and every bonus figure go to 0.00. Silently.
#
# The renewals side is recoverable only if forecast_policy.source_row still
# holds the original CSV row. That is what this script checks.
set -euo pipefail
DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi

echo "=== Has 0016 already been applied? ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT (EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='forecast_policy'
                  AND column_name='primary_assoc_comm_sum'))::text AS already_applied,
       CASE WHEN EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name='forecast_policy'
                           AND column_name='primary_assoc_comm_sum')
            THEN 'nothing to do -- this database is already on the associate basis'
            ELSE 'not yet applied -- continue reading' END AS reading;"

echo
echo "=== SALES: is the associate amount captured? ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT CASE WHEN NOT EXISTS (SELECT 1 FROM information_schema.columns
                             WHERE table_name='sales_transaction'
                               AND column_name='primary_assoc_amount')
            THEN 'STOP: schema has no primary_assoc_amount at all'
            WHEN (SELECT count(*) FROM sales_transaction) = 0
            THEN 'no sales rows -- nothing to migrate, but confirm that is expected'
            ELSE 'column present, see below' END AS status;"

psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT count(*)                                                    AS sales_rows,
       count(*) FILTER (WHERE primary_assoc_amount IS NULL
                          OR primary_assoc_amount = 0)             AS without_associate_amount,
       to_char(COALESCE(SUM(actual_income),0),'FM999,999,990.00')       AS income_now,
       to_char(COALESCE(SUM(primary_assoc_amount),0),'FM999,999,990.00') AS income_after,
       CASE WHEN count(*) = 0 THEN 'no rows'
            WHEN count(*) FILTER (WHERE primary_assoc_amount IS NULL
                                    OR primary_assoc_amount = 0) > count(*) * 0.02
            THEN 'STOP: re-import sales before migrating'
            ELSE 'safe' END                                        AS verdict
FROM sales_transaction WHERE NOT is_excluded;" 2>/dev/null \
  || echo "  (primary_assoc_amount not present -- STOP)"

echo
echo "=== RENEWALS: can the backfill recover the associate commission? ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT count(*)                                                 AS policies,
       count(*) FILTER (WHERE source_row ? 'PrimaryAssocCommSum') AS recoverable,
       count(*) FILTER (WHERE NOT (source_row ? 'PrimaryAssocCommSum')) AS not_recoverable,
       CASE WHEN count(*) = 0 THEN 'no policies -- nothing to migrate'
            WHEN count(*) FILTER (WHERE NOT (source_row ? 'PrimaryAssocCommSum')) = 0
            THEN 'safe: run the backfill immediately after 0016'
            ELSE 'STOP: re-import renewals -- the backfill cannot recover these'
       END                                                      AS verdict
FROM forecast_policy;" 2>/dev/null \
  || echo "  (forecast_policy or source_row not present -- STOP)"

cat <<'NOTE'

Expect the sales income to fall roughly 6 to 7 per cent. That is the point of
the change: the brokerage is the primary associate, so the associate amount is
what it actually receives.

Any STOP means do not migrate. Report the output instead.
NOTE
