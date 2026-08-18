-- Account Manager Income Forecasting Platform
-- Reporting layer. All amounts GST inclusive.
--
-- Two rules are enforced here rather than in application code, so no report can
-- bypass them:
--   1. Canonical manager is resolved by join, never stored on a fact row.
--   2. Achievement is NULL when there is no trustworthy baseline. Never zero.

-- ---------------------------------------------------------------------------
-- Helpers
-- ---------------------------------------------------------------------------

-- Division that yields NULL rather than an error or a misleading zero.
CREATE OR REPLACE FUNCTION safe_div(numerator numeric, denominator numeric)
RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN denominator IS NULL OR denominator = 0
                THEN NULL
                ELSE numerator / denominator END;
$$;

CREATE OR REPLACE FUNCTION au_financial_year(d date)
RETURNS integer LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE WHEN EXTRACT(MONTH FROM d) >= 7
                THEN EXTRACT(YEAR FROM d)::int
                ELSE EXTRACT(YEAR FROM d)::int - 1 END;
$$;

-- Q1 Jul-Sep, Q2 Oct-Dec, Q3 Jan-Mar, Q4 Apr-Jun
CREATE OR REPLACE FUNCTION au_quarter(d date)
RETURNS smallint LANGUAGE sql IMMUTABLE AS $$
    SELECT ((((EXTRACT(MONTH FROM d)::int - 7) % 12 + 12) % 12) / 3 + 1)::smallint;
$$;

-- ---------------------------------------------------------------------------
-- Alias resolution
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_manager_resolution AS
SELECT a.source_manager,
       a.source_manager_norm,
       a.canonical_manager,
       m.status,
       m.include_in_rankings,
       m.include_in_business_totals,
       m.display_order
FROM manager_alias a
JOIN reporting_manager m ON m.canonical_manager = a.canonical_manager
WHERE a.active;

-- ---------------------------------------------------------------------------
-- Actuals
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_sales_reported AS
SELECT t.*, r.canonical_manager, r.include_in_rankings, r.include_in_business_totals
FROM sales_transaction t
LEFT JOIN v_manager_resolution r ON r.source_manager = t.source_manager
WHERE NOT t.is_excluded;

CREATE OR REPLACE VIEW v_actual_month AS
SELECT COALESCE(canonical_manager, source_manager) AS canonical_manager,
       period_month,
       financial_year,
       financial_quarter,
       SUM(positive_income)                          AS positive_actual_income,
       SUM(signed_return_income)                     AS signed_return_income,
       SUM(absolute_return_income)                   AS absolute_return_income,
       SUM(actual_income)                            AS net_actual_income,
       SUM(actual_income) FILTER (WHERE category IN ('RWL','TRW'))       AS actual_renewal_income,
       SUM(actual_income) FILTER (WHERE category = 'N/B')                AS actual_new_business,
       SUM(absolute_return_income) FILTER (WHERE category = 'NCN')       AS new_business_cancellation,
       SUM(absolute_return_income) FILTER (WHERE category = 'LAP')       AS lapse_income_returned,
       SUM(absolute_return_income) FILTER (WHERE category = 'MCN')       AS midterm_cancellation_returned,
       SUM(actual_income) FILTER (WHERE category = 'END' AND actual_income > 0) AS positive_endorsements,
       SUM(absolute_return_income) FILTER (WHERE category = 'END' AND actual_income < 0) AS negative_endorsements,
       SUM(absolute_return_income) FILTER (WHERE category = 'ECN')       AS endorsement_cancellations,
       COUNT(*)                                      AS transaction_rows
FROM v_sales_reported
GROUP BY 1, 2, 3, 4;

-- ---------------------------------------------------------------------------
-- Original forecast, both grains
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_original_forecast_month AS
SELECT COALESCE(r.canonical_manager, o.source_manager) AS canonical_manager,
       o.forecast_month,
       o.financial_year,
       o.financial_quarter,
       o.grain,
       o.origin,
       SUM(o.forecast_contribution) AS original_forecast,
       COUNT(*) FILTER (WHERE o.grain = 'policy') AS original_policy_count
FROM original_forecast o
LEFT JOIN v_manager_resolution r ON r.source_manager = o.source_manager
GROUP BY 1, 2, 3, 4, 5, 6;

-- Per manager-month: is the baseline usable? This single view is what turns
-- July 2026 into N/A instead of zero, and it does so per manager, so one
-- untrustworthy line does not invalidate the whole month.
CREATE OR REPLACE VIEW v_baseline_usable AS
SELECT b.forecast_month,
       b.financial_year,
       b.financial_quarter,
       m.canonical_manager,
       b.baseline_status,
       b.baseline_source,
       (b.baseline_status = 'complete'
        AND NOT b.suppress_achievement
        AND NOT (b.manager_exceptions ? m.canonical_manager)) AS baseline_usable,
       b.note
FROM forecast_baseline b
CROSS JOIN reporting_manager m;

-- ---------------------------------------------------------------------------
-- Growth rate resolution: manager_quarter -> manager -> global
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION resolve_growth(
    p_manager text, p_fy integer, p_quarter smallint)
RETURNS TABLE (basis text, growth_pct numeric, dollar_override numeric, note text)
LANGUAGE sql STABLE AS $$
    SELECT basis, growth_pct, dollar_override, note FROM (
        SELECT 'manager_quarter'::text AS basis, g.growth_pct, g.dollar_override, g.note, 1 AS rank
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager_quarter'
          AND g.canonical_manager = p_manager
          AND g.financial_year = p_fy AND g.financial_quarter = p_quarter
        UNION ALL
        SELECT 'manager', g.growth_pct, g.dollar_override, g.note, 2
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager'
          AND g.canonical_manager = p_manager
          AND (g.financial_year IS NULL OR g.financial_year = p_fy)
        UNION ALL
        SELECT 'global', g.growth_pct, g.dollar_override, g.note, 3
        FROM growth_rate g
        WHERE g.active AND g.scope = 'global'
    ) candidates
    ORDER BY rank
    LIMIT 1;
$$;

-- ---------------------------------------------------------------------------
-- Budget
--   Total Budget = Original Renewal Forecast + New Business Growth Target
-- The budget never moves because the Latest Forecast moved, a policy lapsed,
-- or a cancellation returned income.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_budget_quarter AS
WITH orig AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           SUM(original_forecast) AS original_renewal_forecast
    FROM v_original_forecast_month
    GROUP BY 1, 2, 3
)
SELECT o.canonical_manager,
       o.financial_year,
       o.financial_quarter,
       o.original_renewal_forecast,
       g.basis                                AS growth_basis,
       g.growth_pct,
       g.dollar_override,
       COALESCE(g.dollar_override,
                o.original_renewal_forecast * g.growth_pct) AS new_business_growth_target,
       o.original_renewal_forecast
         + COALESCE(g.dollar_override,
                    o.original_renewal_forecast * g.growth_pct) AS total_budget
FROM orig o
CROSS JOIN LATERAL resolve_growth(o.canonical_manager, o.financial_year,
                                  o.financial_quarter) g;

-- ---------------------------------------------------------------------------
-- Performance. Achievement is NULL wherever the baseline is not usable.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_renewal_performance_month AS
SELECT a.canonical_manager,
       a.period_month,
       a.financial_year,
       a.financial_quarter,
       a.actual_renewal_income,
       o.original_forecast,
       u.baseline_usable,
       u.baseline_source,
       CASE WHEN u.baseline_usable
            THEN a.actual_renewal_income - o.original_forecast END AS renewal_variance,
       CASE WHEN u.baseline_usable
            THEN safe_div(a.actual_renewal_income, o.original_forecast) END AS renewal_achievement
FROM v_actual_month a
LEFT JOIN v_original_forecast_month o
       ON o.canonical_manager = a.canonical_manager
      AND o.forecast_month = a.period_month
LEFT JOIN v_baseline_usable u
       ON u.canonical_manager = a.canonical_manager
      AND u.forecast_month = a.period_month;

CREATE OR REPLACE VIEW v_budget_performance_quarter AS
WITH act AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           SUM(net_actual_income)      AS net_actual_income,
           SUM(positive_actual_income) AS positive_actual_income,
           SUM(absolute_return_income) AS return_income,
           SUM(actual_new_business)    AS actual_new_business
    FROM v_actual_month
    GROUP BY 1, 2, 3
),
-- A quarter is only measurable if every month in it has a usable baseline for
-- that manager. FY2026-27 Q1 therefore reports N/A for Cameron Stewart,
-- Dinghy Scheme and Anastasia K while remaining measurable for everyone else.
usable AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           bool_and(baseline_usable) AS quarter_baseline_usable
    FROM v_baseline_usable
    GROUP BY 1, 2, 3
)
SELECT b.canonical_manager,
       b.financial_year,
       b.financial_quarter,
       b.original_renewal_forecast,
       b.growth_basis,
       b.growth_pct,
       b.new_business_growth_target,
       b.total_budget,
       a.net_actual_income,
       a.positive_actual_income,
       a.return_income,
       a.actual_new_business,
       safe_div(a.return_income, a.positive_actual_income) AS return_pct_of_positive,
       u.quarter_baseline_usable,
       CASE WHEN u.quarter_baseline_usable
            THEN a.net_actual_income - b.total_budget END AS budget_variance,
       CASE WHEN u.quarter_baseline_usable
            THEN safe_div(a.net_actual_income, b.total_budget) END AS budget_achievement
FROM v_budget_quarter b
LEFT JOIN act a  ON a.canonical_manager = b.canonical_manager
                AND a.financial_year = b.financial_year
                AND a.financial_quarter = b.financial_quarter
LEFT JOIN usable u ON u.canonical_manager = b.canonical_manager
                  AND u.financial_year = b.financial_year
                  AND u.financial_quarter = b.financial_quarter;

-- Prior-year actual, exposed as its own comparison metric so management can see
-- that the forecast-based budget deliberately differs from the old
-- prior-year-actual method. Never blended into the renewal forecast.
CREATE OR REPLACE VIEW v_prior_year_comparison AS
SELECT canonical_manager,
       financial_year + 1              AS comparison_financial_year,
       financial_year                  AS prior_financial_year,
       SUM(net_actual_income)          AS prior_year_net_actual_income,
       SUM(positive_actual_income)     AS prior_year_positive_income,
       SUM(absolute_return_income)     AS prior_year_return_income,
       SUM(actual_renewal_income)      AS prior_year_renewal_income,
       SUM(actual_new_business)        AS prior_year_new_business
FROM v_actual_month
GROUP BY canonical_manager, financial_year;
