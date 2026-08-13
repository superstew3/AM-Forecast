#!/usr/bin/env bash
# Is this database carrying the damage?
#
#   bash scripts/check_state.sh
#
# Run it BEFORE and AFTER the rebuild. Uses $DATABASE_URL, which Replit sets.
set -euo pipefail
DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi

psql "$DSN" -X -q <<'SQL'
\pset border 2
SELECT 'forecast months held'          AS measure,
       string_agg(DISTINCT to_char(forecast_month,'Mon YYYY'), ', ') AS value
FROM original_forecast
UNION ALL
SELECT 'original forecast total',
       to_char(COALESCE(SUM(forecast_contribution),0), 'FM999,999,990.00')
FROM original_forecast
UNION ALL
SELECT 'total budget',
       to_char(COALESCE(SUM(total_budget),0), 'FM999,999,990.00')
FROM v_budget_quarter
UNION ALL
SELECT 'managers with a budget', count(*)::text FROM v_budget_quarter
UNION ALL
SELECT 'managers in outlook with NO budget',
       count(*)::text FROM v_outlook_quarter WHERE total_budget IS NULL
UNION ALL
SELECT 'months whose baseline has been lost',
       count(*)::text
FROM forecast_month_coverage c
WHERE c.original_snapshot_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM original_forecast o
                  WHERE o.forecast_month = c.forecast_month)
UNION ALL
SELECT 'duplicate match candidates',
       count(*)::text
FROM (SELECT transaction_id, policy_id, forecast_month, reason
      FROM match_candidate GROUP BY 1,2,3,4 HAVING count(*) > 1) d;
SQL

cat <<'NOTE'

Healthy looks like:
  forecast months held                  Jul 2026, Aug 2026
  original forecast total               460,528.18
  total budget                          495,067.79
  managers with a budget                14
  managers in outlook with NO budget    0
  months whose baseline has been lost   0
  duplicate match candidates            0

Damaged looks like:
  forecast months held                  Jul 2026
  total budget                          281,092.62
  managers with a budget                13
  managers in outlook with NO budget    1
NOTE
