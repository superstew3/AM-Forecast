-- Quarterly bonus.
--
--   Budget Target      = Expected Income x (1 + Growth %)
--   Base Bonus         = (Budget Target - Expected Income) / divisor      [default 3]
--   Above-Target Bonus = (Actual Income - Budget Target) x rate          [default 20%]
--   Total              = 0 when Actual is below Budget Target,
--                        otherwise Base + Above-Target
--
-- The bonus is a quarterly entitlement, so it is settled per quarter. A monthly
-- figure is published too, because managers want to see it accruing, but it is
-- explicitly indicative: monthly figures do not sum to the quarterly bonus,
-- because a quarter can be missed overall while individual months within it were
-- ahead. The quarterly figure is the one that pays.
--
-- The divisor and the above-target rate are settings rather than constants, so
-- changing the scheme is an administrator's decision and not a code change.

ALTER TABLE reporting_settings
    ADD COLUMN IF NOT EXISTS bonus_base_divisor numeric(6,2) NOT NULL DEFAULT 3,
    ADD COLUMN IF NOT EXISTS bonus_above_target_rate numeric(6,4) NOT NULL DEFAULT 0.20;

COMMENT ON COLUMN reporting_settings.bonus_base_divisor IS
    'Base bonus is the monetary growth target divided by this. Default 3.';
COMMENT ON COLUMN reporting_settings.bonus_above_target_rate IS
    'Share of income above the budget target paid as additional bonus. Default 0.20.';

-- --------------------------------------------------------------------------
-- Quarterly bonus
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_bonus_quarter AS
WITH settings AS (
    SELECT bonus_base_divisor AS divisor,
           bonus_above_target_rate AS above_rate,
           date_trunc('month', cut_off_date)::date AS cut_month
    FROM reporting_settings WHERE id = 1
),
budget AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           SUM(original_forecast)  AS expected_income,
           SUM(total_budget)       AS budget_target,
           COUNT(*)                AS months_in_quarter,
           COUNT(*) FILTER (WHERE forecast_month <= (SELECT cut_month FROM settings))
                                   AS months_elapsed,
           SUM(total_budget) FILTER
               (WHERE forecast_month <= (SELECT cut_month FROM settings))
                                   AS budget_to_date,
           bool_or(is_locked)      AS has_locked_months,
           CASE WHEN COUNT(DISTINCT growth_pct) = 1 THEN MIN(growth_pct) END
                                   AS growth_pct
    FROM v_monthly_budget
    GROUP BY 1, 2, 3
),
actual AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           SUM(net_actual_income)      AS actual_income,
           SUM(positive_actual_income) AS positive_income,
           SUM(absolute_return_income) AS return_income
    FROM v_actual_month
    GROUP BY 1, 2, 3
)
SELECT b.canonical_manager,
       b.financial_year,
       b.financial_quarter,
       b.months_in_quarter,
       b.months_elapsed,
       b.months_elapsed >= b.months_in_quarter AS quarter_complete,
       b.months_elapsed > 0                    AS quarter_started,
       b.has_locked_months,
       b.expected_income,
       b.growth_pct,
       b.budget_target,
       b.budget_target - b.expected_income     AS growth_target_amount,
       b.budget_to_date,
       COALESCE(a.actual_income, 0)            AS actual_income,
       a.positive_income,
       a.return_income,
       COALESCE(a.actual_income, 0) - b.budget_target AS above_below_target,
       safe_div(COALESCE(a.actual_income, 0), b.budget_target) AS target_achievement,
       COALESCE(a.actual_income, 0) >= b.budget_target AS target_reached,

       -- The bonus payable if the quarter closed on the figures to date.
       ROUND(CASE
         WHEN b.months_elapsed = 0 THEN NULL
         WHEN COALESCE(a.actual_income, 0) < b.budget_target THEN 0
         ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0)
       END, 2) AS base_bonus,
       ROUND(CASE
         WHEN b.months_elapsed = 0 THEN NULL
         WHEN COALESCE(a.actual_income, 0) < b.budget_target THEN 0
         ELSE (COALESCE(a.actual_income, 0) - b.budget_target) * s.above_rate
       END, 2) AS above_target_bonus,
       ROUND(CASE
         WHEN b.months_elapsed = 0 THEN NULL
         WHEN COALESCE(a.actual_income, 0) < b.budget_target THEN 0
         ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0)
              + (COALESCE(a.actual_income, 0) - b.budget_target) * s.above_rate
       END, 2) AS total_bonus,

       -- What the quarter pays on exactly hitting target. The benchmark a
       -- manager is working towards, before anything above target.
       ROUND((b.budget_target - b.expected_income) / NULLIF(s.divisor, 0), 2)
           AS bonus_at_target,

       -- Still to earn before any bonus is payable at all.
       ROUND(GREATEST(b.budget_target - COALESCE(a.actual_income, 0), 0), 2)
           AS income_still_required,

       -- Where the quarter lands if the pace of the elapsed months continues.
       -- A projection, and labelled as one: it is not money earned.
       ROUND(CASE WHEN b.months_elapsed > 0 AND b.months_elapsed < b.months_in_quarter
                  THEN COALESCE(a.actual_income, 0)
                       * (b.months_in_quarter::numeric / b.months_elapsed)
             END, 2) AS projected_income,
       ROUND(CASE
         WHEN b.months_elapsed = 0 OR b.months_elapsed >= b.months_in_quarter THEN NULL
         WHEN COALESCE(a.actual_income, 0)
              * (b.months_in_quarter::numeric / b.months_elapsed) < b.budget_target
           THEN 0
         ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0)
              + (COALESCE(a.actual_income, 0)
                 * (b.months_in_quarter::numeric / b.months_elapsed)
                 - b.budget_target) * s.above_rate
       END, 2) AS projected_bonus,
       s.divisor    AS bonus_base_divisor,
       s.above_rate AS bonus_above_target_rate
FROM budget b
CROSS JOIN settings s
LEFT JOIN actual a
       ON a.canonical_manager = b.canonical_manager
      AND a.financial_year = b.financial_year
      AND a.financial_quarter = b.financial_quarter;

-- --------------------------------------------------------------------------
-- Monthly indicative bonus
--
-- The same formula applied to a single month. Useful for watching the position
-- build, but it is not what pays: a quarter can be missed overall while months
-- within it were ahead, so these do not sum to the quarterly figure.
-- --------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_bonus_month AS
WITH settings AS (
    SELECT bonus_base_divisor AS divisor, bonus_above_target_rate AS above_rate,
           date_trunc('month', cut_off_date)::date AS cut_month
    FROM reporting_settings WHERE id = 1
)
SELECT b.canonical_manager,
       b.forecast_month AS period_month,
       b.financial_year,
       b.financial_quarter,
       b.forecast_month <= s.cut_month AS month_started,
       b.original_forecast             AS expected_income,
       b.total_budget                  AS budget_target,
       b.total_budget - b.original_forecast AS growth_target_amount,
       a.net_actual_income             AS actual_income,
       CASE WHEN b.forecast_month <= s.cut_month
            THEN COALESCE(a.net_actual_income, 0) >= b.total_budget END
           AS target_reached,
       ROUND(CASE
         WHEN b.forecast_month > s.cut_month THEN NULL
         WHEN COALESCE(a.net_actual_income, 0) < b.total_budget THEN 0
         ELSE (b.total_budget - b.original_forecast) / NULLIF(s.divisor, 0)
              + (COALESCE(a.net_actual_income, 0) - b.total_budget) * s.above_rate
       END, 2) AS indicative_bonus
FROM v_monthly_budget b
CROSS JOIN settings s
LEFT JOIN v_actual_month a
       ON a.canonical_manager = b.canonical_manager
      AND a.period_month = b.forecast_month;
