-- 0019: bonus is paid GST exclusive.
--
-- Every figure this system reports is GST inclusive, and says so. Bonus becomes
-- the single exception, because a bonus is a payment rather than a measure of
-- business written: the money that reaches somebody's pay does not carry the GST
-- the income it was earned on did.
--
-- Only the five payment figures are divided:
--
--     base_bonus, above_target_bonus, total_bonus, bonus_at_target,
--     projected_bonus            (v_bonus_quarter)
--     indicative_bonus           (v_bonus_month)
--
-- Income, targets, variance, achievement, projected income and
-- income_still_required are untouched. They measure performance, not payment,
-- and are compared against each other and against the rest of the application --
-- putting one of them on a different basis would make every comparison wrong in
-- a way nobody would spot.
--
-- The divisor is a setting rather than a literal. A hard-coded 1.1 buried in a
-- view is exactly the shape of the exclusion rules that were hand-copied into
-- four different files in this codebase, each wrong differently and none of them
-- visible. A rate that can change belongs in a table that can be read.
--
-- BECAUSE THIS BREAKS THE GST-INCLUSIVE CONVENTION, EVERY PLACE A BONUS FIGURE
-- IS DISPLAYED MUST SAY "GST EXCLUSIVE". A bonus compared against a GST
-- inclusive target by somebody who was not told is a complaint waiting to
-- happen, and the figures will look wrong by about 9% with no explanation.

ALTER TABLE reporting_settings
    ADD COLUMN IF NOT EXISTS bonus_gst_divisor numeric(6,4) NOT NULL DEFAULT 1.1;

COMMENT ON COLUMN reporting_settings.bonus_gst_divisor IS
    'Bonus payments are divided by this to strip GST. 1.1 for 10% GST. The only '
    'GST-exclusive figures in the system; everything else is GST inclusive.';

ALTER TABLE reporting_settings
    ADD CONSTRAINT reporting_settings_bonus_gst_divisor_positive
    CHECK (bonus_gst_divisor > 0);


-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_bonus_quarter CASCADE;

CREATE VIEW v_bonus_quarter AS
WITH settings AS (
    SELECT bonus_base_divisor AS divisor,
           bonus_above_target_rate AS above_rate,
           bonus_gst_divisor AS gst,
           date_trunc('month', cut_off_date)::date AS cut_month
    FROM reporting_settings WHERE id = 1
), budget AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           SUM(original_forecast) AS expected_income,
           SUM(total_budget) AS budget_target,
           count(*) AS months_in_quarter,
           count(*) FILTER (WHERE forecast_month <= (SELECT cut_month FROM settings))
               AS months_elapsed,
           SUM(total_budget) FILTER (WHERE forecast_month <= (SELECT cut_month FROM settings))
               AS budget_to_date,
           bool_or(is_locked) AS has_locked_months,
           CASE WHEN count(DISTINCT growth_pct) = 1 THEN min(growth_pct) END AS growth_pct
    FROM v_monthly_budget
    GROUP BY canonical_manager, financial_year, financial_quarter
), actual AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           SUM(net_actual_income) AS actual_income,
           SUM(positive_actual_income) AS positive_income,
           SUM(absolute_return_income) AS return_income
    FROM v_actual_month
    GROUP BY canonical_manager, financial_year, financial_quarter
)
SELECT b.canonical_manager,
       b.financial_year,
       b.financial_quarter,
       b.months_in_quarter,
       b.months_elapsed,
       b.months_elapsed >= b.months_in_quarter AS quarter_complete,
       b.months_elapsed > 0                    AS quarter_started,
       b.has_locked_months,
       -- Performance figures. GST inclusive, as everywhere else.
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
       round(GREATEST(b.budget_target - COALESCE(a.actual_income, 0), 0), 2)
                                               AS income_still_required,
       round(CASE WHEN b.months_elapsed > 0 AND b.months_elapsed < b.months_in_quarter
                  THEN COALESCE(a.actual_income, 0)
                       * (b.months_in_quarter::numeric / b.months_elapsed::numeric)
             END, 2)                           AS projected_income,

       -- Payment figures. GST EXCLUSIVE. Divided last, after rounding decisions
       -- that belong to the formula, so the payment is the stated fraction of the
       -- figure it derives from and reconciles by inspection.
       round(CASE
                 WHEN b.months_elapsed = 0 THEN NULL
                 WHEN COALESCE(a.actual_income, 0) < b.budget_target THEN 0
                 ELSE (b.budget_target - b.expected_income)
                      / NULLIF(s.divisor, 0)
             END / s.gst, 2)                   AS base_bonus,
       round(CASE
                 WHEN b.months_elapsed = 0 THEN NULL
                 WHEN COALESCE(a.actual_income, 0) < b.budget_target THEN 0
                 ELSE (COALESCE(a.actual_income, 0) - b.budget_target) * s.above_rate
             END / s.gst, 2)                   AS above_target_bonus,
       round(CASE
                 WHEN b.months_elapsed = 0 THEN NULL
                 WHEN COALESCE(a.actual_income, 0) < b.budget_target THEN 0
                 ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0)
                      + (COALESCE(a.actual_income, 0) - b.budget_target) * s.above_rate
             END / s.gst, 2)                   AS total_bonus,
       round(((b.budget_target - b.expected_income) / NULLIF(s.divisor, 0)) / s.gst, 2)
                                               AS bonus_at_target,
       round(CASE
                 WHEN b.months_elapsed = 0 OR b.months_elapsed >= b.months_in_quarter
                     THEN NULL
                 WHEN (COALESCE(a.actual_income, 0)
                       * (b.months_in_quarter::numeric / b.months_elapsed::numeric))
                      < b.budget_target THEN 0
                 ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0)
                      + (COALESCE(a.actual_income, 0)
                         * (b.months_in_quarter::numeric / b.months_elapsed::numeric)
                         - b.budget_target) * s.above_rate
             END / s.gst, 2)                   AS projected_bonus,

       s.divisor                               AS bonus_base_divisor,
       s.above_rate                            AS bonus_above_target_rate,
       s.gst                                   AS bonus_gst_divisor,
       true                                    AS bonus_is_gst_exclusive
FROM budget b
CROSS JOIN settings s
LEFT JOIN actual a
       ON a.canonical_manager = b.canonical_manager
      AND a.financial_year = b.financial_year
      AND a.financial_quarter = b.financial_quarter;

COMMENT ON VIEW v_bonus_quarter IS
    'Bonus payment figures are GST exclusive; every other figure here is GST '
    'inclusive. bonus_is_gst_exclusive is carried so the interface can label '
    'them without hard-coding the assumption.';


-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_bonus_month CASCADE;

CREATE VIEW v_bonus_month AS
WITH settings AS (
    SELECT bonus_base_divisor AS divisor,
           bonus_above_target_rate AS above_rate,
           bonus_gst_divisor AS gst,
           date_trunc('month', cut_off_date)::date AS cut_month
    FROM reporting_settings WHERE id = 1
)
SELECT b.canonical_manager,
       b.forecast_month                        AS period_month,
       b.financial_year,
       b.financial_quarter,
       b.forecast_month <= s.cut_month         AS month_started,
       b.original_forecast                     AS expected_income,
       b.total_budget                          AS budget_target,
       b.total_budget - b.original_forecast    AS growth_target_amount,
       a.net_actual_income                     AS actual_income,
       CASE WHEN b.forecast_month <= s.cut_month
            THEN COALESCE(a.net_actual_income, 0) >= b.total_budget
       END                                     AS target_reached,
       -- GST exclusive, matching the quarter. A monthly indicative figure on a
       -- different basis from the quarterly payment it anticipates would be read
       -- as an error in one or the other.
       round(CASE
                 WHEN b.forecast_month > s.cut_month THEN NULL
                 WHEN COALESCE(a.net_actual_income, 0) < b.total_budget THEN 0
                 ELSE (b.total_budget - b.original_forecast) / NULLIF(s.divisor, 0)
                      + (COALESCE(a.net_actual_income, 0) - b.total_budget) * s.above_rate
             END / s.gst, 2)                   AS indicative_bonus,
       s.gst                                   AS bonus_gst_divisor,
       true                                    AS bonus_is_gst_exclusive
FROM v_monthly_budget b
CROSS JOIN settings s
LEFT JOIN v_actual_month a
       ON a.canonical_manager = b.canonical_manager
      AND a.period_month = b.forecast_month;

COMMENT ON VIEW v_bonus_month IS
    'Indicative monthly bonus, GST exclusive to match v_bonus_quarter. Monthly '
    'figures do not sum to the quarter: the quarter is assessed as a whole, so a '
    'negative month reduces it rather than showing as a zero-cost month.';
