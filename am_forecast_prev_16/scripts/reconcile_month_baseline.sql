-- Reconciliation for a month's baseline. REPORTS ONLY. Writes nothing.
--
--   psql "$DATABASE_URL" -v month="'2026-08-01'" -f scripts/reconcile_month_baseline.sql
--
-- Run this before a month is used for scoring. It answers, for that month:
-- which source file would be used, what the baseline says now, what it would
-- say recalculated on the associate basis, how many rows and how much value can
-- be rebased, and what cannot.
--
-- A source file must satisfy TWO conditions, not one.
--
--   Uploaded before the month began. A pending-renewals report lists only what
--   has not yet transacted, so a file pulled during the month is already missing
--   everything renewed in it.
--
--   Forward coverage reaching the end of the month. A pending report has a
--   forward window, and an older file may stop short. Tested on a real pair:
--   an April extract satisfied the upload-date rule and held exactly TWO August
--   policies worth $933.03, because its window ended on 1 August. Using it would
--   have replaced a 256-policy, $199,046.67 baseline with almost nothing --
--   silently, and looking for all the world like a clean reconstruction.
--
-- Both conditions are enforced below. Candidates failing either are listed with
-- the reason rather than hidden, so the choice is visible.

\set target_month :month

\echo ''
\echo '=== 1. Candidate source files (renewals uploaded before this month began) ==='
SELECT s.id                                             AS snapshot_id,
       b.id                                             AS batch_id,
       left(coalesce(b.file_name, '(unknown)'), 44)     AS file_name,
       to_char(b.uploaded_at, 'YYYY-MM-DD HH24:MI')     AS uploaded_at,
       count(p.policy_id)                               AS policies_for_month,
       to_char(SUM(p.forecast_contribution), 'FM999,999,990.00') AS associate_basis,
       to_char(SUM(p.gross_expected_income), 'FM999,999,990.00') AS gross_for_audit,
       b.coverage_end                                   AS file_reaches_to,
       CASE
           WHEN b.coverage_end < (:target_month::date + INTERVAL '1 month - 1 day')::date
               THEN 'REJECTED: window stops at ' || b.coverage_end
                    || ', short of the end of the month'
           WHEN s.id = (SELECT s2.id FROM forecast_snapshot s2
                        JOIN upload_batch b2 ON b2.id = s2.batch_id
                        WHERE b2.uploaded_at < (:target_month::date)
                          AND b2.coverage_end >= (:target_month::date
                                                  + INTERVAL '1 month - 1 day')::date
                          AND EXISTS (SELECT 1 FROM forecast_policy p2
                                      WHERE p2.snapshot_id = s2.id
                                        AND p2.forecast_month = :target_month::date
                                        AND NOT p2.is_excluded)
                        ORDER BY b2.uploaded_at DESC, s2.id DESC LIMIT 1)
               THEN '<-- would be used'
           ELSE 'usable, but not the latest' END        AS selection
FROM forecast_snapshot s
JOIN upload_batch b ON b.id = s.batch_id
JOIN forecast_policy p ON p.snapshot_id = s.id
WHERE b.uploaded_at < (:target_month::date)
  AND p.forecast_month = :target_month::date
  AND NOT p.is_excluded
GROUP BY s.id, b.id, b.file_name, b.uploaded_at, b.coverage_end
ORDER BY b.uploaded_at DESC;

\echo ''
\echo '=== 2. Baseline as it stands now ==='
SELECT count(*)                                                  AS baseline_rows,
       to_char(SUM(o.forecast_contribution), 'FM999,999,990.00')  AS current_amount,
       to_char(SUM(o.forecast_contribution) * 1.075, 'FM999,999,990.00') AS current_target,
       string_agg(DISTINCT o.income_basis, ', ')                  AS income_basis,
       string_agg(DISTINCT coalesce(o.established_by, '(unknown)'), ', ') AS established_by
FROM original_forecast o
WHERE o.forecast_month = :target_month::date;

\echo ''
\echo '=== 3. Recalculated from the source file, on the associate basis ==='
WITH src AS (
    SELECT p.*
    FROM forecast_policy p
    JOIN forecast_snapshot s ON s.id = p.snapshot_id
    JOIN upload_batch b      ON b.id = s.batch_id
    WHERE p.forecast_month = :target_month::date
      AND NOT p.is_excluded
      AND b.uploaded_at = (SELECT MAX(b2.uploaded_at) FROM forecast_snapshot s2
                           JOIN upload_batch b2 ON b2.id = s2.batch_id
                           JOIN forecast_policy p2 ON p2.snapshot_id = s2.id
                           WHERE b2.uploaded_at < (:target_month::date)
                             AND b2.coverage_end >= (:target_month::date
                                                     + INTERVAL '1 month - 1 day')::date
                             AND p2.forecast_month = :target_month::date
                             AND NOT p2.is_excluded)
)
SELECT count(*)                                                   AS policies,
       to_char(SUM(forecast_contribution), 'FM999,999,990.00')     AS recalculated_amount,
       to_char(SUM(forecast_contribution) * 1.075, 'FM999,999,990.00') AS recalculated_target,
       to_char(SUM(gross_expected_income), 'FM999,999,990.00')     AS gross_for_audit,
       to_char(SUM(forecast_contribution)
               - (SELECT COALESCE(SUM(o.forecast_contribution), 0) FROM original_forecast o
                  WHERE o.forecast_month = :target_month::date), 'FM999,999,990.00')
                                                                   AS movement_vs_current
FROM src;

\echo ''
\echo '=== 4. Rows that can and cannot be rebased ==='
SELECT count(*)                                                   AS baseline_rows,
       count(*) FILTER (WHERE p.policy_id IS NOT NULL)            AS rebaseable_rows,
       count(*) FILTER (WHERE p.policy_id IS NULL)                AS unresolved_rows,
       to_char(COALESCE(SUM(o.forecast_contribution)
               FILTER (WHERE p.policy_id IS NOT NULL), 0), 'FM999,999,990.00')
                                                                   AS rebaseable_value,
       to_char(COALESCE(SUM(o.forecast_contribution)
               FILTER (WHERE p.policy_id IS NULL), 0), 'FM999,999,990.00')
                                                                   AS unresolved_value
FROM original_forecast o
LEFT JOIN forecast_policy p
       ON p.policy_id = o.policy_id AND p.forecast_month = o.forecast_month
WHERE o.forecast_month = :target_month::date;

\echo ''
\echo '=== 5. Unresolved rows in detail (these block scoring) ==='
SELECT o.source_manager, o.client_code, o.policy_number, o.class_abbrev,
       to_char(o.forecast_contribution, 'FM999,990.00') AS amount,
       coalesce(o.established_by, '(unknown)')          AS established_by
FROM original_forecast o
LEFT JOIN forecast_policy p
       ON p.policy_id = o.policy_id AND p.forecast_month = o.forecast_month
WHERE o.forecast_month = :target_month::date
  AND p.policy_id IS NULL
ORDER BY o.forecast_contribution DESC
LIMIT 25;

\echo ''
\echo ''
\echo '=== 6. Verdict ==='
SELECT CASE
    WHEN NOT EXISTS (SELECT 1 FROM forecast_snapshot s2
                     JOIN upload_batch b2 ON b2.id = s2.batch_id
                     JOIN forecast_policy p2 ON p2.snapshot_id = s2.id
                     WHERE b2.uploaded_at < (:target_month::date)
                       AND b2.coverage_end >= (:target_month::date
                                               + INTERVAL '1 month - 1 day')::date
                       AND p2.forecast_month = :target_month::date
                       AND NOT p2.is_excluded)
        THEN 'STOP: no file both predates the month and covers all of it. '
             || 'Reconstruction would replace the baseline with a partial window.'
    ELSE 'A usable source exists. Compare sections 2 and 3 before applying: a '
         || 'large negative movement means the window is short, not that the '
         || 'book shrank.' END AS verdict;

\echo ''
\echo 'Nothing above has been changed. To apply, use reconstruct_month_baseline.sql'
\echo 'which records the before and after figures against an audited override.'
