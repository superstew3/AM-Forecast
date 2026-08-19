-- 0020: retire the reporting cut-off as the actual/expected boundary.
--
-- Six views decided what counted as complete by reading reporting_settings
-- .cut_off_date, a value somebody had to advance by hand every month. When it
-- was wrong everything downstream was wrong, silently: it sat at 2025-12-31 for
-- a week after a test run set it, and nothing on any screen said so.
--
-- 0018 replaced that judgement with the calendar in Australia/Melbourne, which
-- cannot be left stale. This cuts the remaining views over to it.
--
-- The views affected, and why each cared:
--
--   v_outlook_month            actual for elapsed months, expected thereafter
--   v_forecast_position_month  the same split, for the forecast position
--   v_renewal_income_month     which months count as transacted
--   v_bonus_quarter            months_elapsed and budget_to_date -- the pace
--                              figure on the bonus tracker, so a stale cut-off
--                              made every manager's pace wrong
--   v_bonus_month              whether a month has started
--   v_forecast_month_writable  dropped entirely, see below
--
-- cut_off_date is NOT dropped. It still records what the last complete month was
-- taken to be, which is worth keeping for reference and for anything reading it
-- outside these views. It simply stops deciding anything.

-- ---------------------------------------------------------------------------
-- The near-identical pair, resolved.
-- ---------------------------------------------------------------------------
-- A VIEW called v_forecast_month_writable and a FUNCTION called
-- forecast_month_writable() disagreed: the view read the cut-off and reported
-- August writable, the function read the calendar and reported it closed. The
-- upload path used the function, so anything trusting the view got the opposite
-- answer from the code that actually enforces it.
--
-- Two things one character apart, on different bases, giving contradictory
-- answers about whether a target can be overwritten. The view goes; the function
-- is the one the importer enforces.
DROP VIEW IF EXISTS v_forecast_month_writable CASCADE;

CREATE VIEW v_forecast_month_writable AS
SELECT m.forecast_month,
       month_state(m.forecast_month)            AS month_state,
       forecast_month_is_open(m.forecast_month) AS is_open,
       forecast_month_writable(m.forecast_month) AS is_writable,
       EXISTS (SELECT 1 FROM forecast_month_override o
               WHERE o.forecast_month = m.forecast_month
                 AND o.consumed_at IS NULL)     AS override_pending
FROM (SELECT DISTINCT forecast_month FROM original_forecast
      UNION SELECT DISTINCT forecast_month FROM forecast_policy) m;

COMMENT ON VIEW v_forecast_month_writable IS
    'Now a thin wrapper over the same functions the importer enforces, so the '
    'view and the code cannot disagree. It previously read the cut-off and '
    'contradicted forecast_month_writable() about the current month.';

-- ---------------------------------------------------------------------------
-- The six views, cut over.
-- ---------------------------------------------------------------------------
-- Each carried a cut_month CTE reading the stored cut-off. Only that
-- expression changes; the surrounding logic is untouched, so the cut-over
-- cannot quietly alter a calculation while it changes the boundary.

DROP VIEW IF EXISTS v_outlook_month CASCADE;
CREATE VIEW v_outlook_month AS
 WITH cut AS (
         SELECT reporting_current_month() AS cut_month
           FROM reporting_settings
          WHERE reporting_settings.id = 1
        ), periods AS (
         SELECT v_actual_month.canonical_manager,
            v_actual_month.period_month AS month,
            v_actual_month.financial_year,
            v_actual_month.financial_quarter,
            v_actual_month.net_actual_income,
            NULL::numeric AS latest_forecast,
            'actual'::text AS basis
           FROM v_actual_month,
            cut
          WHERE v_actual_month.period_month <= cut.cut_month
        UNION ALL
         SELECT v_latest_forecast_month.canonical_manager,
            v_latest_forecast_month.forecast_month,
            v_latest_forecast_month.financial_year,
            v_latest_forecast_month.financial_quarter,
            NULL::numeric AS "numeric",
            v_latest_forecast_month.latest_forecast,
            'forecast'::text AS text
           FROM v_latest_forecast_month,
            cut
          WHERE v_latest_forecast_month.forecast_month > cut.cut_month
        )
 SELECT canonical_manager,
    month,
    financial_year,
    financial_quarter,
    basis,
    COALESCE(net_actual_income, 0::numeric) AS net_actual_income,
    COALESCE(latest_forecast, 0::numeric) AS latest_forecast,
    COALESCE(net_actual_income, latest_forecast, 0::numeric) AS outlook_income
   FROM periods;

DROP VIEW IF EXISTS v_forecast_position_month CASCADE;
CREATE VIEW v_forecast_position_month AS
 WITH cut AS (
         SELECT reporting_current_month() AS cut_month
           FROM reporting_settings
          WHERE reporting_settings.id = 1
        ), pos AS (
         SELECT COALESCE(o.canonical_manager, l.canonical_manager) AS canonical_manager,
            COALESCE(o.forecast_month, l.forecast_month) AS forecast_month,
            COALESCE(o.financial_year, l.financial_year) AS financial_year,
            COALESCE(o.financial_quarter, l.financial_quarter) AS financial_quarter,
            COALESCE(sum(o.original_forecast), 0::numeric) AS original_forecast,
            sum(l.latest_forecast) AS latest_forecast_raw
           FROM v_original_forecast_month o
             FULL JOIN v_latest_forecast_month l ON l.canonical_manager::text = o.canonical_manager::text AND l.forecast_month = o.forecast_month
          GROUP BY (COALESCE(o.canonical_manager, l.canonical_manager)), (COALESCE(o.forecast_month, l.forecast_month)), (COALESCE(o.financial_year, l.financial_year)), (COALESCE(o.financial_quarter, l.financial_quarter))
        )
 SELECT p.canonical_manager,
    p.forecast_month,
    p.financial_year,
    p.financial_quarter,
    p.original_forecast,
    p.forecast_month > cut.cut_month AS is_future_period,
        CASE
            WHEN p.forecast_month > cut.cut_month THEN COALESCE(p.latest_forecast_raw, 0::numeric)
            ELSE NULL::numeric
        END AS latest_forecast,
        CASE
            WHEN p.forecast_month > cut.cut_month THEN COALESCE(p.latest_forecast_raw, 0::numeric) - p.original_forecast
            ELSE NULL::numeric
        END AS forecast_movement
   FROM pos p
     CROSS JOIN cut;

DROP VIEW IF EXISTS v_renewal_income_month CASCADE;
CREATE VIEW v_renewal_income_month AS
 WITH actual AS (
         SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
            t.period_month,
            t.financial_year,
            t.financial_quarter,
            sum(t.actual_income) AS renewal_income,
            sum(t.actual_income) FILTER (WHERE t.category::text = 'RWL'::text) AS renewal_only,
            sum(t.actual_income) FILTER (WHERE t.category::text = 'TRW'::text) AS transfer_only,
            count(*) AS renewal_transactions
           FROM sales_transaction t
             LEFT JOIN v_manager_resolution r ON r.source_manager::text = t.source_manager::text
          WHERE NOT t.is_excluded AND (t.category::text = ANY (ARRAY['RWL'::character varying::text, 'TRW'::character varying::text]))
          GROUP BY (COALESCE(r.canonical_manager, t.source_manager)), t.period_month, t.financial_year, t.financial_quarter
        ), forecast AS (
         SELECT v_original_forecast_month.canonical_manager,
            v_original_forecast_month.forecast_month,
            sum(v_original_forecast_month.original_forecast) AS original_forecast
           FROM v_original_forecast_month
          GROUP BY v_original_forecast_month.canonical_manager, v_original_forecast_month.forecast_month
        ), cut AS (
         SELECT reporting_current_month() AS cut_month
           FROM reporting_settings
          WHERE reporting_settings.id = 1
        )
 SELECT COALESCE(a.canonical_manager, f.canonical_manager) AS canonical_manager,
    COALESCE(a.period_month, f.forecast_month) AS period_month,
    au_financial_year(COALESCE(a.period_month, f.forecast_month)) AS financial_year,
    au_quarter(COALESCE(a.period_month, f.forecast_month)) AS financial_quarter,
    COALESCE(a.period_month, f.forecast_month) <= cut.cut_month AS period_started,
    a.renewal_income,
    a.renewal_only,
    a.transfer_only,
    a.renewal_transactions,
    f.original_forecast,
        CASE
            WHEN COALESCE(a.period_month, f.forecast_month) <= cut.cut_month AND f.original_forecast IS NOT NULL THEN COALESCE(a.renewal_income, 0::numeric) - f.original_forecast
            ELSE NULL::numeric
        END AS renewal_variance,
        CASE
            WHEN COALESCE(a.period_month, f.forecast_month) <= cut.cut_month THEN safe_div(COALESCE(a.renewal_income, 0::numeric), f.original_forecast)
            ELSE NULL::numeric
        END AS renewal_achievement
   FROM actual a
     FULL JOIN forecast f ON f.canonical_manager::text = a.canonical_manager::text AND f.forecast_month = a.period_month
     CROSS JOIN cut;

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
            count(*) FILTER (WHERE v_monthly_budget.forecast_month <= (( SELECT settings.cut_month
                   FROM settings))) AS months_elapsed,
            sum(v_monthly_budget.total_budget) FILTER (WHERE v_monthly_budget.forecast_month <= (( SELECT settings.cut_month
                   FROM settings))) AS budget_to_date,
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

DROP VIEW IF EXISTS v_bonus_month CASCADE;
CREATE VIEW v_bonus_month AS
 WITH settings AS (
         SELECT reporting_settings.bonus_base_divisor AS divisor,
            reporting_settings.bonus_above_target_rate AS above_rate,
            reporting_settings.bonus_gst_divisor AS gst,
            reporting_current_month() AS cut_month
           FROM reporting_settings
          WHERE reporting_settings.id = 1
        )
 SELECT b.canonical_manager,
    b.forecast_month AS period_month,
    b.financial_year,
    b.financial_quarter,
    b.forecast_month <= s.cut_month AS month_started,
    b.original_forecast AS expected_income,
    b.total_budget AS budget_target,
    b.total_budget - b.original_forecast AS growth_target_amount,
    a.net_actual_income AS actual_income,
        CASE
            WHEN b.forecast_month <= s.cut_month THEN COALESCE(a.net_actual_income, 0::numeric) >= b.total_budget
            ELSE NULL::boolean
        END AS target_reached,
    round(
        CASE
            WHEN b.forecast_month > s.cut_month THEN NULL::numeric
            WHEN COALESCE(a.net_actual_income, 0::numeric) < b.total_budget THEN 0::numeric
            ELSE (b.total_budget - b.original_forecast) / NULLIF(s.divisor, 0::numeric) + (COALESCE(a.net_actual_income, 0::numeric) - b.total_budget) * s.above_rate
        END / s.gst, 2) AS indicative_bonus,
    s.gst AS bonus_gst_divisor,
    true AS bonus_is_gst_exclusive
   FROM v_monthly_budget b
     CROSS JOIN settings s
     LEFT JOIN v_actual_month a ON a.canonical_manager::text = b.canonical_manager::text AND a.period_month = b.forecast_month;

DROP VIEW IF EXISTS v_outlook_quarter CASCADE;
CREATE VIEW v_outlook_quarter AS
 SELECT o.canonical_manager,
    o.financial_year,
    o.financial_quarter,
    sum(o.outlook_income) FILTER (WHERE o.basis = 'actual'::text) AS completed_actual,
    sum(o.outlook_income) FILTER (WHERE o.basis = 'forecast'::text) AS future_latest_forecast,
    sum(o.outlook_income) AS latest_outlook,
    b.total_budget,
    b.total_budget - sum(o.outlook_income) AS remaining_budget_gap
   FROM v_outlook_month o
     LEFT JOIN v_budget_quarter b ON b.canonical_manager::text = o.canonical_manager::text AND b.financial_year = o.financial_year AND b.financial_quarter = o.financial_quarter
  GROUP BY o.canonical_manager, o.financial_year, o.financial_quarter, b.total_budget;

DROP VIEW IF EXISTS v_business_dashboard CASCADE;
CREATE VIEW v_business_dashboard AS
 WITH a AS (
         SELECT v_actual_month.financial_year,
            sum(v_actual_month.net_actual_income) AS net_actual_income,
            sum(v_actual_month.positive_actual_income) AS positive_actual_income,
            sum(v_actual_month.absolute_return_income) AS return_income,
            sum(v_actual_month.actual_new_business) AS actual_new_business,
            sum(v_actual_month.new_business_cancellation) AS new_business_cancellation,
            sum(v_actual_month.lapse_income_returned) AS lapse_income_returned,
            sum(v_actual_month.midterm_cancellation_returned) AS midterm_cancellation_returned,
            sum(v_actual_month.negative_endorsements) AS negative_endorsements,
            sum(v_actual_month.endorsement_cancellations) AS endorsement_cancellations
           FROM v_actual_month
          GROUP BY v_actual_month.financial_year
        ), f AS (
         SELECT v_forecast_position_month.financial_year,
            sum(v_forecast_position_month.original_forecast) AS original_renewal_forecast,
            sum(v_forecast_position_month.latest_forecast) AS latest_renewal_forecast,
            sum(v_forecast_position_month.forecast_movement) AS forecast_movement
           FROM v_forecast_position_month
          GROUP BY v_forecast_position_month.financial_year
        ), b AS (
         SELECT v_budget_quarter.financial_year,
            sum(v_budget_quarter.total_budget) AS total_budget
           FROM v_budget_quarter
          GROUP BY v_budget_quarter.financial_year
        ), o AS (
         SELECT v_outlook_quarter.financial_year,
            sum(v_outlook_quarter.latest_outlook) AS latest_outlook
           FROM v_outlook_quarter
          GROUP BY v_outlook_quarter.financial_year
        )
 SELECT COALESCE(a.financial_year, f.financial_year, b.financial_year) AS financial_year,
    pc.coverage_status,
    pc.label AS period_label,
    a.net_actual_income,
    a.positive_actual_income,
    a.return_income,
    f.original_renewal_forecast,
    f.latest_renewal_forecast,
    f.forecast_movement,
    b.total_budget,
    safe_div(a.net_actual_income, b.total_budget) AS budget_achievement,
    o.latest_outlook,
    b.total_budget - o.latest_outlook AS remaining_budget_gap,
    a.actual_new_business,
    a.lapse_income_returned,
    a.midterm_cancellation_returned,
    a.new_business_cancellation,
    a.negative_endorsements,
    a.endorsement_cancellations,
    'All income figures are GST inclusive.'::text AS gst_note
   FROM a
     FULL JOIN f ON f.financial_year = a.financial_year
     FULL JOIN b ON b.financial_year = COALESCE(a.financial_year, f.financial_year)
     FULL JOIN o ON o.financial_year = COALESCE(a.financial_year, f.financial_year)
     LEFT JOIN period_coverage pc ON pc.financial_year = COALESCE(a.financial_year, f.financial_year) AND pc.data_domain::text = 'actuals'::text;
