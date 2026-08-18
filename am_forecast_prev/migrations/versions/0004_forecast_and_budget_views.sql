-- Stage 3: Latest Forecast, movement reporting, monthly budget allocation and
-- Latest Outlook. All amounts GST inclusive.

-- ---------------------------------------------------------------------------
-- Latest Forecast
--   For each future month, the newest accepted snapshot covering it.
--   Completed months are not carried here: they report actuals.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_latest_forecast_policy AS
SELECT p.*,
       c.latest_snapshot_id,
       COALESCE(r.canonical_manager, p.source_manager) AS canonical_manager
FROM forecast_month_coverage c
JOIN forecast_policy p
  ON p.snapshot_id = c.latest_snapshot_id
 AND p.forecast_month = c.forecast_month
LEFT JOIN v_manager_resolution r ON r.source_manager = p.source_manager
WHERE NOT p.is_excluded;

CREATE OR REPLACE VIEW v_latest_forecast_month AS
SELECT canonical_manager,
       forecast_month,
       financial_year,
       financial_quarter,
       SUM(forecast_contribution) AS latest_forecast,
       SUM(raw_expected_income)   AS latest_raw_expected,
       COUNT(*)                   AS policy_count,
       COUNT(*) FILTER (WHERE cardinality(exception_flags) > 0) AS exception_policies
FROM v_latest_forecast_policy
GROUP BY 1, 2, 3, 4;

-- Original against Latest, per manager and month. Movement is reported, not
-- absorbed: a fall in Latest is a fact about the book, not a correction.
-- A completed month has no Latest Forecast: it reports actuals. Latest and
-- movement are NULL there, not zero. Reporting a completed month's Latest as
-- zero would manufacture a forecast collapse that never happened -- July 2026
-- would show a $348k adverse movement purely because its renewals had already
-- transacted.
CREATE OR REPLACE VIEW v_forecast_position_month AS
WITH cut AS (SELECT date_trunc('month', cut_off_date)::date AS cut_month
             FROM reporting_settings WHERE id = 1),
pos AS (
    SELECT COALESCE(o.canonical_manager, l.canonical_manager) AS canonical_manager,
           COALESCE(o.forecast_month, l.forecast_month)       AS forecast_month,
           COALESCE(o.financial_year, l.financial_year)       AS financial_year,
           COALESCE(o.financial_quarter, l.financial_quarter) AS financial_quarter,
           COALESCE(SUM(o.original_forecast), 0)              AS original_forecast,
           SUM(l.latest_forecast)                             AS latest_forecast_raw
    FROM v_original_forecast_month o
    FULL OUTER JOIN v_latest_forecast_month l
      ON l.canonical_manager = o.canonical_manager
     AND l.forecast_month = o.forecast_month
    GROUP BY 1, 2, 3, 4
)
SELECT p.canonical_manager, p.forecast_month, p.financial_year, p.financial_quarter,
       p.original_forecast,
       p.forecast_month > cut.cut_month AS is_future_period,
       CASE WHEN p.forecast_month > cut.cut_month
            THEN COALESCE(p.latest_forecast_raw, 0) END AS latest_forecast,
       CASE WHEN p.forecast_month > cut.cut_month
            THEN COALESCE(p.latest_forecast_raw, 0) - p.original_forecast END
                                                        AS forecast_movement
FROM pos p CROSS JOIN cut;

-- ---------------------------------------------------------------------------
-- Movement analysis (reporting view C)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_forecast_movement_detail AS
SELECT m.*,
       COALESCE(rt.canonical_manager, m.to_manager)   AS canonical_to_manager,
       COALESCE(rf.canonical_manager, m.from_manager) AS canonical_from_manager,
       p.client_code, p.policy_number, p.class_abbrev, p.underwriter_abbrev,
       p.expiry_date
FROM forecast_movement m
LEFT JOIN v_manager_resolution rt ON rt.source_manager = m.to_manager
LEFT JOIN v_manager_resolution rf ON rf.source_manager = m.from_manager
LEFT JOIN LATERAL (
    SELECT client_code, policy_number, class_abbrev, underwriter_abbrev, expiry_date
    FROM forecast_policy fp
    WHERE fp.policy_id = m.policy_id
    ORDER BY fp.snapshot_id DESC LIMIT 1
) p ON true;

CREATE OR REPLACE VIEW v_forecast_movement_summary AS
SELECT forecast_month,
       COALESCE(canonical_from_manager, canonical_to_manager) AS canonical_manager,
       SUM(original_income)                                   AS original_expected_income,
       COUNT(*) FILTER (WHERE movement_type = 'removed_from_latest')  AS policies_removed,
       COALESCE(SUM(previous_income)
                FILTER (WHERE movement_type = 'removed_from_latest'), 0)
                                                              AS expected_income_removed,
       COUNT(*) FILTER (WHERE movement_type = 'added_after_original') AS policies_added,
       COALESCE(SUM(latest_income)
                FILTER (WHERE movement_type = 'added_after_original'), 0)
                                                              AS expected_income_added,
       COALESCE(SUM(movement_amount)
                FILTER (WHERE movement_type = 'amount_changed'), 0)
                                                              AS amount_changes,
       COUNT(*) FILTER (WHERE movement_type = 'manager_changed')      AS manager_transfers,
       SUM(latest_income)                                     AS latest_expected_income
FROM v_forecast_movement_detail
GROUP BY 1, 2;

-- ---------------------------------------------------------------------------
-- Monthly budget allocation
--
-- The quarterly new business growth target is spread across the quarter by each
-- month's share of that quarter's Original Renewal Forecast, not in equal
-- thirds. The renewal pattern is materially uneven: FY2026-27 Q4 carries
-- $1.10M against Q1's $963k, and within Q2 December ($141k) is roughly a third
-- of November ($381k). An equal split would set targets that bear no relation
-- to when the book actually renews.
--
-- Where a quarter has no original forecast at all, the target falls back to an
-- equal split across its months rather than dividing by zero.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_monthly_budget AS
WITH monthly AS (
    SELECT canonical_manager, forecast_month, financial_year, financial_quarter,
           SUM(original_forecast) AS original_forecast
    FROM v_original_forecast_month
    GROUP BY 1, 2, 3, 4
),
quarterly AS (
    SELECT canonical_manager, financial_year, financial_quarter,
           SUM(original_forecast) AS quarter_original,
           COUNT(*)               AS months_in_quarter
    FROM monthly GROUP BY 1, 2, 3
),
allocated AS (
    SELECT m.canonical_manager, m.forecast_month, m.financial_year, m.financial_quarter,
           m.original_forecast,
           q.quarter_original,
           b.new_business_growth_target AS quarter_growth_target,
           b.growth_basis,
           CASE
             WHEN q.quarter_original > 0
               THEN b.new_business_growth_target * (m.original_forecast / q.quarter_original)
             ELSE b.new_business_growth_target / NULLIF(q.months_in_quarter, 0)
           END AS allocated_growth_target,
           CASE WHEN q.quarter_original > 0 THEN 'forecast_weighted'
                ELSE 'equal_split' END AS allocation_method
    FROM monthly m
    JOIN quarterly q
      ON q.canonical_manager = m.canonical_manager
     AND q.financial_year = m.financial_year
     AND q.financial_quarter = m.financial_quarter
    JOIN v_budget_quarter b
      ON b.canonical_manager = m.canonical_manager
     AND b.financial_year = m.financial_year
     AND b.financial_quarter = m.financial_quarter
)
SELECT a.canonical_manager,
       a.forecast_month,
       a.financial_year,
       a.financial_quarter,
       a.original_forecast,
       a.growth_basis,
       a.allocation_method,
       a.allocated_growth_target                          AS calculated_growth_target,
       o.override_amount,
       COALESCE(o.override_amount, a.allocated_growth_target) AS new_business_growth_target,
       o.override_amount IS NOT NULL                      AS is_overridden,
       o.reason                                           AS override_reason,
       a.original_forecast
         + COALESCE(o.override_amount, a.allocated_growth_target) AS total_budget
FROM allocated a
LEFT JOIN monthly_target_override o
       ON o.canonical_manager = a.canonical_manager
      AND o.target_month = a.forecast_month
      AND o.active;

-- ---------------------------------------------------------------------------
-- Latest Outlook
--   Completed-period Net Actual Income + Latest Forecast for future periods.
--   Contains no assumed future new business. New business is recognised only
--   when it appears in Sales Transactions.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_outlook_month AS
WITH cut AS (SELECT date_trunc('month', cut_off_date)::date AS cut_month
             FROM reporting_settings WHERE id = 1),
periods AS (
    SELECT canonical_manager, period_month AS month, financial_year, financial_quarter,
           net_actual_income, NULL::numeric AS latest_forecast, 'actual' AS basis
    FROM v_actual_month, cut
    WHERE period_month <= cut.cut_month
    UNION ALL
    SELECT canonical_manager, forecast_month, financial_year, financial_quarter,
           NULL, latest_forecast, 'forecast'
    FROM v_latest_forecast_month, cut
    WHERE forecast_month > cut.cut_month
)
SELECT canonical_manager, month, financial_year, financial_quarter, basis,
       COALESCE(net_actual_income, 0) AS net_actual_income,
       COALESCE(latest_forecast, 0)   AS latest_forecast,
       COALESCE(net_actual_income, latest_forecast, 0) AS outlook_income
FROM periods;

CREATE OR REPLACE VIEW v_outlook_quarter AS
SELECT o.canonical_manager,
       o.financial_year,
       o.financial_quarter,
       SUM(o.outlook_income) FILTER (WHERE o.basis = 'actual')   AS completed_actual,
       SUM(o.outlook_income) FILTER (WHERE o.basis = 'forecast') AS future_latest_forecast,
       SUM(o.outlook_income)                                     AS latest_outlook,
       b.total_budget,
       b.total_budget - SUM(o.outlook_income)                    AS remaining_budget_gap
FROM v_outlook_month o
LEFT JOIN v_budget_quarter b
       ON b.canonical_manager = o.canonical_manager
      AND b.financial_year = o.financial_year
      AND b.financial_quarter = o.financial_quarter
GROUP BY 1, 2, 3, b.total_budget;

-- ---------------------------------------------------------------------------
-- Business dashboard (reporting view A)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_business_dashboard AS
WITH a AS (
    SELECT financial_year,
           SUM(net_actual_income)              AS net_actual_income,
           SUM(positive_actual_income)         AS positive_actual_income,
           SUM(absolute_return_income)         AS return_income,
           SUM(actual_new_business)            AS actual_new_business,
           SUM(new_business_cancellation)      AS new_business_cancellation,
           SUM(lapse_income_returned)          AS lapse_income_returned,
           SUM(midterm_cancellation_returned)  AS midterm_cancellation_returned,
           SUM(negative_endorsements)          AS negative_endorsements,
           SUM(endorsement_cancellations)      AS endorsement_cancellations
    FROM v_actual_month GROUP BY 1
),
f AS (
    SELECT financial_year,
           SUM(original_forecast) AS original_renewal_forecast,
           SUM(latest_forecast)   AS latest_renewal_forecast,
           SUM(forecast_movement) AS forecast_movement
    FROM v_forecast_position_month GROUP BY 1
),
b AS (
    SELECT financial_year, SUM(total_budget) AS total_budget
    FROM v_budget_quarter GROUP BY 1
),
o AS (
    SELECT financial_year, SUM(latest_outlook) AS latest_outlook
    FROM v_outlook_quarter GROUP BY 1
)
SELECT COALESCE(a.financial_year, f.financial_year, b.financial_year) AS financial_year,
       pc.coverage_status, pc.label AS period_label,
       a.net_actual_income, a.positive_actual_income, a.return_income,
       f.original_renewal_forecast, f.latest_renewal_forecast, f.forecast_movement,
       b.total_budget,
       safe_div(a.net_actual_income, b.total_budget) AS budget_achievement,
       o.latest_outlook,
       b.total_budget - o.latest_outlook AS remaining_budget_gap,
       a.actual_new_business, a.lapse_income_returned,
       a.midterm_cancellation_returned, a.new_business_cancellation,
       a.negative_endorsements, a.endorsement_cancellations,
       'All income figures are GST inclusive.' AS gst_note
FROM a
FULL OUTER JOIN f ON f.financial_year = a.financial_year
FULL OUTER JOIN b ON b.financial_year = COALESCE(a.financial_year, f.financial_year)
FULL OUTER JOIN o ON o.financial_year = COALESCE(a.financial_year, f.financial_year)
LEFT JOIN period_coverage pc
       ON pc.financial_year = COALESCE(a.financial_year, f.financial_year)
      AND pc.data_domain = 'actuals';

-- ---------------------------------------------------------------------------
-- Return income analysis (reporting view D)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_return_income_analysis AS
SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
       t.financial_year, t.financial_quarter, t.period_month,
       t.derived_classification,
       SUM(t.signed_return_income)   AS signed_return_income,
       SUM(t.absolute_return_income) AS absolute_return_income,
       COUNT(*)                      AS transaction_rows
FROM sales_transaction t
LEFT JOIN v_manager_resolution r ON r.source_manager = t.source_manager
WHERE NOT t.is_excluded AND t.actual_income < 0
GROUP BY 1, 2, 3, 4, 5;

-- ---------------------------------------------------------------------------
-- New business analysis (reporting view E). No future new business is forecast.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_new_business_analysis AS
WITH nb AS (
    SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
           t.financial_year, t.financial_quarter,
           SUM(t.actual_income) FILTER (WHERE t.category = 'N/B' AND t.actual_income > 0)
               AS gross_new_business,
           SUM(t.absolute_return_income) FILTER
               (WHERE t.category = 'N/B' AND t.actual_income < 0)
               AS negative_new_business_corrections,
           SUM(t.absolute_return_income) FILTER (WHERE t.category = 'NCN')
               AS new_business_cancellations,
           SUM(t.actual_income) FILTER (WHERE t.category IN ('N/B', 'NCN'))
               AS net_new_business
    FROM sales_transaction t
    LEFT JOIN v_manager_resolution r ON r.source_manager = t.source_manager
    WHERE NOT t.is_excluded
    GROUP BY 1, 2, 3
)
SELECT nb.*,
       b.new_business_growth_target,
       safe_div(nb.net_new_business, b.new_business_growth_target) AS growth_target_achievement
FROM nb
LEFT JOIN v_budget_quarter b
       ON b.canonical_manager = nb.canonical_manager
      AND b.financial_year = nb.financial_year
      AND b.financial_quarter = nb.financial_quarter;
