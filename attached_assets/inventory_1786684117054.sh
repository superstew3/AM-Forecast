#!/usr/bin/env bash
# What is actually in this database? No assumptions, no expected values.
#
#   bash scripts/inventory.sh
#
# Written after two rounds of guessing wrong about a deployment's state from a
# distance. Every query here is schema-aware: it reports what exists rather than
# failing when something does not.
set -euo pipefail
DSN="${DATABASE_URL:-${DSN:-}}"
if [ -z "$DSN" ]; then echo "No DATABASE_URL or DSN set."; exit 1; fi

echo "=== Which database am I actually connected to? ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT current_database() AS database, current_user AS role,
       inet_server_addr()::text AS host, inet_server_port() AS port;"

echo
echo "=== Migration level, inferred from the schema ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT 'forecast_month_coverage (0001)' AS marker,
       (to_regclass('forecast_month_coverage') IS NOT NULL)::text AS present
UNION ALL SELECT 'reporting_settings.bonus_base_divisor (0013)',
       (EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='reporting_settings'
                  AND column_name='bonus_base_divisor'))::text
UNION ALL SELECT 'app_user (0014)',
       (to_regclass('app_user') IS NOT NULL)::text
UNION ALL SELECT 'sales_transaction.gross_income (0016)',
       (EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='sales_transaction' AND column_name='gross_income'))::text
UNION ALL SELECT 'forecast_policy.primary_assoc_comm_sum (0016)',
       (EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name='forecast_policy' AND column_name='primary_assoc_comm_sum'))::text
UNION ALL SELECT 'forecast_month_lock (0017)',
       (to_regclass('forecast_month_lock') IS NOT NULL)::text;"

echo
echo "=== Row counts in every table that carries data ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT relname AS table_name, n_live_tup AS approx_rows
FROM pg_stat_user_tables
WHERE n_live_tup > 0 ORDER BY n_live_tup DESC LIMIT 30;"

echo
echo "=== Empty tables that would normally hold data ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT relname AS table_name
FROM pg_stat_user_tables
WHERE n_live_tup = 0
  AND relname IN ('sales_transaction','forecast_policy','forecast_snapshot',
                  'original_forecast','upload_batch','match_allocation',
                  'reporting_manager','app_user')
ORDER BY relname;"

echo
echo "=== Upload history: what was loaded, and what was rolled back ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT id, file_type, status,
       left(coalesce(file_name,''), 40) AS file_name,
       to_char(uploaded_at, 'YYYY-MM-DD HH24:MI') AS uploaded,
       coalesce(rolled_back_by,'') AS rolled_back_by,
       coalesce(left(rollback_reason, 30),'') AS rollback_reason
FROM upload_batch ORDER BY id;" 2>/dev/null \
  || echo "  (no upload_batch table)"

echo
echo "=== Reporting settings ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT cut_off_date, cut_off_set_by,
       to_char(cut_off_set_at,'YYYY-MM-DD HH24:MI') AS cut_off_set_at
FROM reporting_settings WHERE id = 1;" 2>/dev/null \
  || echo "  (no reporting_settings)"

echo
echo "=== Where the outlook rows come from ==="
psql "$DSN" -X -q -c "\pset border 2" -c "
SELECT financial_year AS fy, financial_quarter AS q,
       count(*) AS outlook_rows,
       to_char(COALESCE(SUM(completed_actual),0),'FM999,999,990.00')      AS from_actuals,
       to_char(COALESCE(SUM(future_latest_forecast),0),'FM999,999,990.00') AS from_forecast,
       to_char(COALESCE(SUM(total_budget),0),'FM999,999,990.00')          AS budget
FROM v_outlook_quarter GROUP BY 1,2 ORDER BY 1,2;" 2>/dev/null \
  || echo "  (v_outlook_quarter not available)"

cat <<'NOTE'

Send this whole output back before anything is migrated, backed up or changed.
It replaces guessing about what this deployment contains.
NOTE
