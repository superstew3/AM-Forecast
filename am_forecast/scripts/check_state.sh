#!/usr/bin/env bash
# Is this database carrying the damage?
#
#   bash scripts/check_state.sh
#
# Uses $DATABASE_URL, which Replit sets.
#
# The first version of this script counted every outlook row without a budget,
# anywhere. That is wrong on the real book: a financial year with actuals but no
# forecast baseline -- a prior year, typically -- has no budget by design, and
# every manager in it counts as an orphan. It reported a healthy full dataset as
# damaged. The check below is scoped to quarters that actually carry a budget,
# which is where budget and outlook are supposed to reconcile.
set -euo pipefail
DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi

echo "=== Damage checks: all three must be zero ==="
psql "$DSN" -X -q <<'SQL'
\pset border 2
SELECT 'months whose baseline has been lost' AS check, count(*)::text AS value
FROM forecast_month_coverage c
WHERE c.original_snapshot_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM original_forecast o
                  WHERE o.forecast_month = c.forecast_month)
UNION ALL
SELECT 'duplicate match candidates', count(*)::text
FROM (SELECT transaction_id, policy_id, forecast_month, reason
      FROM match_candidate GROUP BY 1,2,3,4 HAVING count(*) > 1) d
UNION ALL
-- A manager with an outlook but no budget, in a quarter that HAS budgets. This
-- is the reconciliation break: the two totals stop agreeing with the sum of the
-- per-manager rows, and the manager shows no target rather than a target of
-- zero. Quarters with no budget at all are excluded -- see the header.
SELECT 'unbudgeted managers in a budgeted quarter', count(*)::text
FROM v_outlook_quarter o
WHERE o.total_budget IS NULL
  AND EXISTS (SELECT 1 FROM v_budget_quarter b
              WHERE b.financial_year = o.financial_year
                AND b.financial_quarter = o.financial_quarter);
SQL

echo
echo "=== Shape of the book: read it, do not compare to a fixed number ==="
psql "$DSN" -X -q <<'SQL'
\pset border 2
SELECT o.financial_year                                  AS fy,
       o.financial_quarter                               AS q,
       count(*)                                          AS outlook_rows,
       (SELECT count(*) FROM v_budget_quarter b
        WHERE b.financial_year = o.financial_year
          AND b.financial_quarter = o.financial_quarter) AS budget_rows,
       count(*) FILTER (WHERE o.total_budget IS NULL)    AS no_budget,
       CASE WHEN NOT EXISTS (SELECT 1 FROM v_budget_quarter b
                             WHERE b.financial_year = o.financial_year
                               AND b.financial_quarter = o.financial_quarter)
            THEN 'no forecast baseline (expected for a prior year)'
            WHEN count(*) FILTER (WHERE o.total_budget IS NULL) > 0
            THEN 'INVESTIGATE: budgeted quarter, unbudgeted managers'
            ELSE 'reconciles' END                        AS reading
FROM v_outlook_quarter o
GROUP BY 1, 2 ORDER BY 1, 2;
SQL

echo
echo "=== Totals ==="
psql "$DSN" -X -q <<'SQL'
\pset border 2
SELECT 'forecast months held' AS measure, count(DISTINCT forecast_month)::text AS value
FROM original_forecast
UNION ALL
SELECT 'forecast range',
       to_char(min(forecast_month),'Mon YYYY') || ' to ' || to_char(max(forecast_month),'Mon YYYY')
FROM original_forecast
UNION ALL
SELECT 'original forecast total',
       to_char(COALESCE(SUM(forecast_contribution),0), 'FM999,999,990.00')
FROM original_forecast
UNION ALL
SELECT 'total budget',
       to_char(COALESCE(SUM(total_budget),0), 'FM999,999,990.00')
FROM v_budget_quarter;
SQL

cat <<'NOTE'

How to read this:

  All three damage checks zero      this database is not carrying the rollback
                                    or matcher defects.

  "no forecast baseline"            expected wherever there are actuals but no
                                    forecast was ever loaded for that year.
                                    Not damage.

  "INVESTIGATE"                     a manager has income but no target in a
                                    quarter that is budgeted. Either their
                                    forecast is missing, or that month's
                                    baseline was lost. Worth naming who.
NOTE
