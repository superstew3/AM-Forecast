-- Renewal achievement without policy-level matching.
--
-- The first version measured renewal achievement from matched policy outcomes,
-- which needs a forecast period overlapping transacted actuals. With only two
-- policies in scope for July 2026, every manager showed N/A — technically
-- correct and completely useless.
--
-- The workbook this replaces measured it far more simply: actual renewal income
-- for the month against the forecast for that month, at manager-month level. No
-- policy matching required, and it works from the first upload. That is what
-- this view does. Policy-level matching remains available for retention detail,
-- where it answers a different and finer question.

CREATE OR REPLACE VIEW v_renewal_income_month AS
WITH actual AS (
    SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
           t.period_month,
           t.financial_year,
           t.financial_quarter,
           SUM(t.actual_income)                                   AS renewal_income,
           SUM(t.actual_income) FILTER (WHERE t.category = 'RWL') AS renewal_only,
           SUM(t.actual_income) FILTER (WHERE t.category = 'TRW') AS transfer_only,
           COUNT(*)                                               AS renewal_transactions
    FROM sales_transaction t
    LEFT JOIN v_manager_resolution r ON r.source_manager = t.source_manager
    WHERE NOT t.is_excluded AND t.category IN ('RWL', 'TRW')
    GROUP BY 1, 2, 3, 4
),
forecast AS (
    SELECT canonical_manager, forecast_month, SUM(original_forecast) AS original_forecast
    FROM v_original_forecast_month
    GROUP BY 1, 2
),
cut AS (SELECT date_trunc('month', cut_off_date)::date AS cut_month
        FROM reporting_settings WHERE id = 1)
SELECT COALESCE(a.canonical_manager, f.canonical_manager) AS canonical_manager,
       COALESCE(a.period_month, f.forecast_month)         AS period_month,
       au_financial_year(COALESCE(a.period_month, f.forecast_month))  AS financial_year,
       au_quarter(COALESCE(a.period_month, f.forecast_month))         AS financial_quarter,
       COALESCE(a.period_month, f.forecast_month) <= cut.cut_month    AS period_started,
       a.renewal_income,
       a.renewal_only,
       a.transfer_only,
       a.renewal_transactions,
       f.original_forecast,
       -- Only measurable once the period has started and a baseline exists.
       CASE WHEN COALESCE(a.period_month, f.forecast_month) <= cut.cut_month
              AND f.original_forecast IS NOT NULL
            THEN COALESCE(a.renewal_income, 0) - f.original_forecast END
           AS renewal_variance,
       CASE WHEN COALESCE(a.period_month, f.forecast_month) <= cut.cut_month
            THEN safe_div(COALESCE(a.renewal_income, 0), f.original_forecast) END
           AS renewal_achievement
FROM actual a
FULL OUTER JOIN forecast f
  ON f.canonical_manager = a.canonical_manager
 AND f.forecast_month = a.period_month
CROSS JOIN cut;
