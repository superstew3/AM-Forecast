-- 0022: the month under way is not an elapsed month.
--
-- v_bonus_quarter counted "months elapsed" as every month up to AND INCLUDING
-- the current one. So on 19 August, with July closed and August three weeks in,
-- it compared JULY'S INCOME against JULY PLUS AUGUST'S TARGET.
--
-- Every manager read as behind, most of them "well behind", and the projected
-- bonus for the whole business came out at zero. On the live book:
--
--     AnneM Goodchild   income to date  $20,855.09
--                       target to date  $36,724.15   (July 18,012.72 + August 18,711.43)
--                       pace                  56.8%  "well behind"
--
-- Measured against July alone -- the only month that has actually finished --
-- she is on $20,855.09 against $18,012.72, which is 115.8% and ahead. The page
-- said the opposite about almost everybody.
--
-- This is the same fault the reporting rules warn about, in a new place: a whole
-- period's target against a part period's income. It was invisible while the
-- August figures were absent for a different reason, and it would have been
-- wrong every month of the year regardless.
--
-- The rule now: only COMPLETED months are compared. Strictly before the current
-- month, never including it. actual_income_completed is added alongside the
-- running total so the two can never be confused again -- one is what has been
-- earned, the other is what can be judged.
--
-- The whole-quarter figures are untouched. Bonus still pays on the quarter's
-- full target, as it always has; this changes only what "to date" means.

DROP VIEW IF EXISTS v_bonus_quarter CASCADE;

CREATE VIEW v_bonus_quarter AS
 WITH settings AS (
         SELECT reporting_settings.bonus_base_divisor AS divisor,
            reporting_settings.bonus_above_target_rate AS above_rate,
            reporting_settings.bonus_gst_divisor AS gst,
            reporting_current_month() AS cut_month
           FROM reporting_settings
          WHERE reporting_settings.id = 1
        ), budget AS (
         SELECT v_monthly_budget.canonical_manager,
            v_monthly_budget.financial_year,
            v_monthly_budget.financial_quarter,
            sum(v_monthly_budget.original_forecast) AS expected_income,
            sum(v_monthly_budget.total_budget) AS budget_target,
            count(*) AS months_in_quarter,
            count(*) FILTER (WHERE v_monthly_budget.forecast_month < reporting_current_month()) AS months_elapsed,
            sum(v_monthly_budget.total_budget) FILTER (WHERE v_monthly_budget.forecast_month < reporting_current_month()) AS budget_to_date,
            bool_or(v_monthly_budget.is_locked) AS has_locked_months,
                CASE
                    WHEN count(DISTINCT v_monthly_budget.growth_pct) = 1 THEN min(v_monthly_budget.growth_pct)
                    ELSE NULL::numeric
                END AS growth_pct
           FROM v_monthly_budget
          GROUP BY v_monthly_budget.canonical_manager, v_monthly_budget.financial_year, v_monthly_budget.financial_quarter
        ), actual AS (
         SELECT v_actual_month.canonical_manager,
            v_actual_month.financial_year,
            v_actual_month.financial_quarter,
            sum(v_actual_month.net_actual_income) AS actual_income,
            sum(v_actual_month.net_actual_income) FILTER (WHERE v_actual_month.period_month < reporting_current_month()) AS actual_income_completed,
            sum(v_actual_month.positive_actual_income) AS positive_income,
            sum(v_actual_month.absolute_return_income) AS return_income
           FROM v_actual_month
          GROUP BY v_actual_month.canonical_manager, v_actual_month.financial_year, v_actual_month.financial_quarter
        )
 SELECT b.canonical_manager,
    b.financial_year,
    b.financial_quarter,
    b.months_in_quarter,
    b.months_elapsed,
    b.months_elapsed >= b.months_in_quarter AS quarter_complete,
    b.months_elapsed > 0 AS quarter_started,
    b.has_locked_months,
    b.expected_income,
    b.growth_pct,
    b.budget_target,
    b.budget_target - b.expected_income AS growth_target_amount,
    b.budget_to_date,
    COALESCE(a.actual_income, 0::numeric) AS actual_income,
    -- Income from completed months only. Kept beside the running total rather
    -- than replacing it: one is what has been earned so far, the other is what
    -- can be fairly judged, and conflating them is what caused this.
    a.actual_income_completed,
    a.positive_income,
    a.return_income,
    COALESCE(a.actual_income, 0::numeric) - b.budget_target AS above_below_target,
    safe_div(COALESCE(a.actual_income, 0::numeric), b.budget_target) AS target_achievement,
    COALESCE(a.actual_income, 0::numeric) >= b.budget_target AS target_reached,
    round(GREATEST(b.budget_target - COALESCE(a.actual_income, 0::numeric), 0::numeric), 2) AS income_still_required,
    round(
        CASE
            WHEN b.months_elapsed > 0 AND b.months_elapsed < b.months_in_quarter THEN COALESCE(a.actual_income, 0::numeric) * (b.months_in_quarter::numeric / b.months_elapsed::numeric)
            ELSE NULL::numeric
        END, 2) AS projected_income,
    round(
        CASE
            WHEN b.months_elapsed = 0 THEN NULL::numeric
            WHEN COALESCE(a.actual_income, 0::numeric) < b.budget_target THEN 0::numeric
            ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric)
        END / s.gst, 2) AS base_bonus,
    round(
        CASE
            WHEN b.months_elapsed = 0 THEN NULL::numeric
            WHEN COALESCE(a.actual_income, 0::numeric) < b.budget_target THEN 0::numeric
            ELSE (COALESCE(a.actual_income, 0::numeric) - b.budget_target) * s.above_rate
        END / s.gst, 2) AS above_target_bonus,
    round(
        CASE
            WHEN b.months_elapsed = 0 THEN NULL::numeric
            WHEN COALESCE(a.actual_income, 0::numeric) < b.budget_target THEN 0::numeric
            ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric) + (COALESCE(a.actual_income, 0::numeric) - b.budget_target) * s.above_rate
        END / s.gst, 2) AS total_bonus,
    round((b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric) / s.gst, 2) AS bonus_at_target,
    round(
        CASE
            WHEN b.months_elapsed = 0 OR b.months_elapsed >= b.months_in_quarter THEN NULL::numeric
            WHEN (COALESCE(a.actual_income, 0::numeric) * (b.months_in_quarter::numeric / b.months_elapsed::numeric)) < b.budget_target THEN 0::numeric
            ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric) + (COALESCE(a.actual_income, 0::numeric) * (b.months_in_quarter::numeric / b.months_elapsed::numeric) - b.budget_target) * s.above_rate
        END / s.gst, 2) AS projected_bonus,
    s.divisor AS bonus_base_divisor,
    s.above_rate AS bonus_above_target_rate,
    s.gst AS bonus_gst_divisor,
    true AS bonus_is_gst_exclusive
   FROM budget b
     CROSS JOIN settings s
     LEFT JOIN actual a ON a.canonical_manager::text = b.canonical_manager::text AND a.financial_year = b.financial_year AND a.financial_quarter = b.financial_quarter;

COMMENT ON VIEW v_bonus_quarter IS
    'months_elapsed and budget_to_date count COMPLETED months only. The month '
    'under way is excluded: including it measured a part month''s income against '
    'a whole month''s target and reported nearly every manager as well behind.';
