-- 0023: project by pace, not by counting months.
--
-- The projection multiplied income to date by months_in_quarter / months_elapsed
-- -- one month in, times three. That assumes every month in a quarter carries an
-- equal share of the target. None of them do.
--
-- On the live book it got two managers backwards at once:
--
--     Michael Stewart   91.5% of pace, BEHIND      flat x3 -> 207,150  BONUS
--                                                  by pace ->  176,660  none
--     AnneM Goodchild  115.8% of pace, AHEAD       flat x3 ->  62,565  none
--                                                  by pace ->   74,877  BONUS
--
-- July is 39% of Michael's quarter and 28% of AnneM's. Tripling a month that
-- large overstates; tripling one that small understates. He was projected a
-- bonus for a month he missed, and she was projected none for a month she beat.
--
-- Projecting by PACE instead -- the ratio of income to the target for the months
-- that have finished, applied to the whole quarter's target -- carries the shape
-- of the forecast with it. A manager running at 91.5% is projected to finish at
-- 91.5% of target, which is the sentence anybody would expect the number to mean.
--
-- It also rests on actual_income_completed, matching the pace figure beside it,
-- so the two can never tell different stories. And a projection with no completed
-- month behind it is now null rather than zero: without one it is not a
-- projection, it is a guess.

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
    a.actual_income_completed,
    a.positive_income,
    a.return_income,
    COALESCE(a.actual_income, 0::numeric) - b.budget_target AS above_below_target,
    safe_div(COALESCE(a.actual_income, 0::numeric), b.budget_target) AS target_achievement,
    COALESCE(a.actual_income, 0::numeric) >= b.budget_target AS target_reached,
    round(GREATEST(b.budget_target - COALESCE(a.actual_income, 0::numeric), 0::numeric), 2) AS income_still_required,
    round(
        CASE
            WHEN b.months_elapsed > 0 AND b.months_elapsed < b.months_in_quarter
                 AND a.actual_income_completed IS NOT NULL THEN (a.actual_income_completed * (b.budget_target / NULLIF(b.budget_to_date, 0::numeric)))
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
            WHEN b.months_elapsed = 0 OR b.months_elapsed >= b.months_in_quarter
                 OR a.actual_income_completed IS NULL THEN NULL::numeric
            WHEN ((a.actual_income_completed * (b.budget_target / NULLIF(b.budget_to_date, 0::numeric)))) < b.budget_target THEN 0::numeric
            ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric) + ((a.actual_income_completed * (b.budget_target / NULLIF(b.budget_to_date, 0::numeric))) - b.budget_target) * s.above_rate
        END / s.gst, 2) AS projected_bonus,
    s.divisor AS bonus_base_divisor,
    s.above_rate AS bonus_above_target_rate,
    s.gst AS bonus_gst_divisor,
    true AS bonus_is_gst_exclusive
   FROM budget b
     CROSS JOIN settings s
     LEFT JOIN actual a ON a.canonical_manager::text = b.canonical_manager::text AND a.financial_year = b.financial_year AND a.financial_quarter = b.financial_quarter;

COMMENT ON VIEW v_bonus_quarter IS
    'Pace and projection both rest on completed months only. The projection '
    'scales the quarter target by the pace achieved, rather than multiplying '
    'income by a month count -- monthly targets are uneven, so the month count '
    'projected a bonus for managers who were behind and none for some who were '
    'ahead.';
