-- Budget precision.
--
-- The growth target is a percentage of the Original Renewal Forecast, so it
-- carries fractions of a cent: $3,701,892.60 x 7.5% = $277,641.945.
--
-- Rounding at manager-and-quarter grain was tried and rejected. Fifty-six
-- separate roundings accumulate, moving the FY2026-27 Total Budget from
-- $3,979,534.55 to $3,979,534.57 and breaking the confirmed figure.
--
-- Full precision is therefore retained through the calculation and every
-- aggregate, so a drill-down sums exactly to its parent and the confirmed
-- position holds to the cent. Rounding happens at display and is the interface's
-- job. Exports carry the unrounded values deliberately.
--
-- This migration restores the unrounded definitions after that experiment.

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
       g.basis        AS growth_basis,
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
       a.allocated_growth_target                              AS calculated_growth_target,
       o.override_amount,
       COALESCE(o.override_amount, a.allocated_growth_target) AS new_business_growth_target,
       o.override_amount IS NOT NULL                          AS is_overridden,
       o.reason                                               AS override_reason,
       a.original_forecast
         + COALESCE(o.override_amount, a.allocated_growth_target) AS total_budget
FROM allocated a
LEFT JOIN monthly_target_override o
       ON o.canonical_manager = a.canonical_manager
      AND o.target_month = a.forecast_month
      AND o.active;
