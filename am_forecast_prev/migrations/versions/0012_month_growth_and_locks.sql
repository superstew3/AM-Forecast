-- Growth percentage at month level, and budget locking.
--
-- Two additions.
--
-- 1. A growth percentage can now be set for one manager and one month, not just
--    for a manager or a manager-and-quarter. Resolution runs most specific
--    first: manager_month, then manager_quarter, then manager, then global.
--
-- 2. A month's budget can be locked. A locked month keeps the exact figure it
--    held when locked, and stops moving even if the forecast beneath it
--    changes. This is what makes a budget safe to agree with a manager: once
--    the number is signed off it cannot drift, and unlocking is a deliberate,
--    audited act rather than a side effect of the next upload.

ALTER TABLE growth_rate
    ADD COLUMN IF NOT EXISTS target_month date;

ALTER TABLE growth_rate DROP CONSTRAINT IF EXISTS ck_growth_rate_growth_scope;
ALTER TABLE growth_rate DROP CONSTRAINT IF EXISTS ck_growth_rate_growth_scope_consistency;

ALTER TABLE growth_rate ADD CONSTRAINT ck_growth_rate_growth_scope
    CHECK (scope IN ('global', 'manager', 'manager_quarter', 'manager_month'));

ALTER TABLE growth_rate ADD CONSTRAINT ck_growth_rate_growth_scope_consistency CHECK (
    (scope = 'global'
       AND canonical_manager IS NULL AND financial_quarter IS NULL
       AND target_month IS NULL)
 OR (scope = 'manager'
       AND canonical_manager IS NOT NULL AND financial_quarter IS NULL
       AND target_month IS NULL)
 OR (scope = 'manager_quarter'
       AND canonical_manager IS NOT NULL AND financial_year IS NOT NULL
       AND financial_quarter IS NOT NULL AND target_month IS NULL)
 OR (scope = 'manager_month'
       AND canonical_manager IS NOT NULL AND target_month IS NOT NULL));

CREATE UNIQUE INDEX IF NOT EXISTS uq_growth_manager_month
    ON growth_rate (canonical_manager, target_month)
    WHERE scope = 'manager_month' AND active;

-- A month's budget, frozen at the figure it held when locked.
CREATE TABLE IF NOT EXISTS budget_lock (
    id                bigserial PRIMARY KEY,
    canonical_manager varchar(120) NOT NULL
        REFERENCES reporting_manager(canonical_manager) ON UPDATE CASCADE,
    target_month      date NOT NULL,
    -- The whole budget as at the moment of locking, not just its parts. Storing
    -- the components too keeps the lock explainable a year later.
    locked_budget           numeric(14,2) NOT NULL,
    locked_renewal_forecast numeric(14,2) NOT NULL,
    locked_growth_target    numeric(14,2) NOT NULL,
    locked_growth_pct       numeric(6,4),
    reason      text NOT NULL,
    locked_by   varchar(120) NOT NULL,
    locked_at   timestamptz NOT NULL DEFAULT now(),
    active      boolean NOT NULL DEFAULT true,
    unlocked_by varchar(120),
    unlocked_at timestamptz,
    unlock_reason text
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_budget_lock_active
    ON budget_lock (canonical_manager, target_month) WHERE active;
CREATE INDEX IF NOT EXISTS ix_budget_lock_month ON budget_lock (target_month) WHERE active;

-- Resolution, most specific first.
CREATE OR REPLACE FUNCTION resolve_growth_month(
    p_manager text, p_month date)
RETURNS TABLE (basis text, growth_pct numeric, dollar_override numeric, note text)
LANGUAGE sql STABLE AS $$
    SELECT basis, growth_pct, dollar_override, note FROM (
        SELECT 'manager_month'::text AS basis, g.growth_pct, g.dollar_override,
               g.note, 1 AS rank
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager_month'
          AND g.canonical_manager = p_manager AND g.target_month = p_month
        UNION ALL
        SELECT 'manager_quarter', g.growth_pct, g.dollar_override, g.note, 2
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager_quarter'
          AND g.canonical_manager = p_manager
          AND g.financial_year = au_financial_year(p_month)
          AND g.financial_quarter = au_quarter(p_month)
        UNION ALL
        SELECT 'manager', g.growth_pct, g.dollar_override, g.note, 3
        FROM growth_rate g
        WHERE g.active AND g.scope = 'manager'
          AND g.canonical_manager = p_manager
          AND (g.financial_year IS NULL
               OR g.financial_year = au_financial_year(p_month))
        UNION ALL
        SELECT 'global', g.growth_pct, g.dollar_override, g.note, 4
        FROM growth_rate g
        WHERE g.active AND g.scope = 'global'
    ) candidates
    ORDER BY rank
    LIMIT 1;
$$;

-- The quarterly view previously fed the monthly one. It now rolls up from it,
-- so the pair is rebuilt in the new order along with everything downstream.
-- CASCADE rather than a fixed order: the dependency between the monthly and
-- quarterly views reverses in this migration, so which one must go first
-- depends on whether it has run before. Everything dropped here is recreated
-- below, in dependency order.
DROP VIEW IF EXISTS v_monthly_budget CASCADE;
DROP VIEW IF EXISTS v_budget_quarter CASCADE;

-- Monthly budget, resolved per month and honouring locks.
--
-- A percentage applied to the month's own forecast gives the same answer as
-- allocating the quarter's target by each month's share of that quarter, so
-- nothing changes for managers on a flat percentage. Where a quarter carries a
-- dollar override the share-based allocation is still used, because a fixed
-- dollar target has to be spread rather than recomputed.
CREATE VIEW v_monthly_budget AS
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
resolved AS (
    SELECT m.*, q.quarter_original, q.months_in_quarter,
           g.basis AS growth_basis, g.growth_pct, g.dollar_override
    FROM monthly m
    JOIN quarterly q
      ON q.canonical_manager = m.canonical_manager
     AND q.financial_year = m.financial_year
     AND q.financial_quarter = m.financial_quarter
    CROSS JOIN LATERAL resolve_growth_month(m.canonical_manager, m.forecast_month) g
),
calculated AS (
    SELECT r.*,
           CASE
             WHEN r.dollar_override IS NOT NULL AND r.growth_basis = 'manager_month'
               THEN r.dollar_override
             WHEN r.dollar_override IS NOT NULL AND r.quarter_original > 0
               THEN r.dollar_override * (r.original_forecast / r.quarter_original)
             WHEN r.dollar_override IS NOT NULL
               THEN r.dollar_override / NULLIF(r.months_in_quarter, 0)
             ELSE r.original_forecast * r.growth_pct
           END AS calculated_growth_target,
           CASE WHEN r.dollar_override IS NOT NULL THEN 'dollar_override'
                ELSE 'growth_percentage' END AS allocation_method
    FROM resolved r
)
SELECT c.canonical_manager,
       c.forecast_month,
       c.financial_year,
       c.financial_quarter,
       c.original_forecast,
       c.growth_basis,
       c.growth_pct,
       c.allocation_method,
       c.calculated_growth_target,
       o.override_amount,
       l.locked_budget IS NOT NULL                     AS is_locked,
       l.locked_at,
       l.locked_by,
       l.reason                                        AS lock_reason,
       COALESCE(o.override_amount, c.calculated_growth_target)
                                                       AS new_business_growth_target,
       o.override_amount IS NOT NULL                   AS is_overridden,
       o.reason                                        AS override_reason,
       -- A locked month keeps the exact figure it held when locked.
       COALESCE(l.locked_budget,
                c.original_forecast
                  + COALESCE(o.override_amount, c.calculated_growth_target))
                                                       AS total_budget
FROM calculated c
LEFT JOIN monthly_target_override o
       ON o.canonical_manager = c.canonical_manager
      AND o.target_month = c.forecast_month
      AND o.active
LEFT JOIN budget_lock l
       ON l.canonical_manager = c.canonical_manager
      AND l.target_month = c.forecast_month
      AND l.active;

-- Quarterly budget now rolls up from the months, so month-level percentages and
-- locks are reflected rather than bypassed.
CREATE VIEW v_budget_quarter AS
SELECT canonical_manager,
       financial_year,
       financial_quarter,
       SUM(original_forecast)             AS original_renewal_forecast,
       -- Where a quarter's months disagree the basis is reported as 'mixed', so
       -- a single label never implies a uniformity that is not there.
       CASE WHEN COUNT(DISTINCT growth_basis) = 1 THEN MIN(growth_basis)
            ELSE 'mixed' END              AS growth_basis,
       CASE WHEN COUNT(DISTINCT growth_pct) = 1 THEN MIN(growth_pct) END AS growth_pct,
       NULLIF(SUM(override_amount), 0)    AS dollar_override,
       SUM(new_business_growth_target)    AS new_business_growth_target,
       SUM(total_budget)                  AS total_budget,
       bool_or(is_locked)                 AS has_locked_months,
       COUNT(*) FILTER (WHERE is_locked)  AS locked_months
FROM v_monthly_budget
GROUP BY 1, 2, 3;

-- Dependent views, restored unchanged and in dependency order.
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

CREATE VIEW v_budget_performance_quarter AS
 WITH act AS (
         SELECT v_actual_month.canonical_manager,
            v_actual_month.financial_year,
            v_actual_month.financial_quarter,
            sum(v_actual_month.net_actual_income) AS net_actual_income,
            sum(v_actual_month.positive_actual_income) AS positive_actual_income,
            sum(v_actual_month.absolute_return_income) AS return_income,
            sum(v_actual_month.actual_new_business) AS actual_new_business
           FROM v_actual_month
          GROUP BY v_actual_month.canonical_manager, v_actual_month.financial_year, v_actual_month.financial_quarter
        ), usable AS (
         SELECT v_baseline_usable.canonical_manager,
            v_baseline_usable.financial_year,
            v_baseline_usable.financial_quarter,
            bool_and(v_baseline_usable.baseline_usable) AS quarter_baseline_usable
           FROM v_baseline_usable
          GROUP BY v_baseline_usable.canonical_manager, v_baseline_usable.financial_year, v_baseline_usable.financial_quarter
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
        CASE
            WHEN u.quarter_baseline_usable THEN a.net_actual_income - b.total_budget
            ELSE NULL::numeric
        END AS budget_variance,
        CASE
            WHEN u.quarter_baseline_usable THEN safe_div(a.net_actual_income, b.total_budget)
            ELSE NULL::numeric
        END AS budget_achievement
   FROM v_budget_quarter b
     LEFT JOIN act a ON a.canonical_manager::text = b.canonical_manager::text AND a.financial_year = b.financial_year AND a.financial_quarter = b.financial_quarter
     LEFT JOIN usable u ON u.canonical_manager::text = b.canonical_manager::text AND u.financial_year = b.financial_year AND u.financial_quarter = b.financial_quarter;

CREATE VIEW v_new_business_analysis AS
 WITH nb AS (
         SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
            t.financial_year,
            t.financial_quarter,
            sum(t.actual_income) FILTER (WHERE t.category::text = 'N/B'::text AND t.actual_income > 0::numeric) AS gross_new_business,
            sum(t.absolute_return_income) FILTER (WHERE t.category::text = 'N/B'::text AND t.actual_income < 0::numeric) AS negative_new_business_corrections,
            sum(t.absolute_return_income) FILTER (WHERE t.category::text = 'NCN'::text) AS new_business_cancellations,
            sum(t.actual_income) FILTER (WHERE t.category::text = ANY (ARRAY['N/B'::character varying, 'NCN'::character varying]::text[])) AS net_new_business
           FROM sales_transaction t
             LEFT JOIN v_manager_resolution r ON r.source_manager::text = t.source_manager::text
          WHERE NOT t.is_excluded
          GROUP BY (COALESCE(r.canonical_manager, t.source_manager)), t.financial_year, t.financial_quarter
        )
 SELECT nb.canonical_manager,
    nb.financial_year,
    nb.financial_quarter,
    nb.gross_new_business,
    nb.negative_new_business_corrections,
    nb.new_business_cancellations,
    nb.net_new_business,
    b.new_business_growth_target,
    safe_div(nb.net_new_business, b.new_business_growth_target) AS growth_target_achievement
   FROM nb
     LEFT JOIN v_budget_quarter b ON b.canonical_manager::text = nb.canonical_manager::text AND b.financial_year = nb.financial_year AND b.financial_quarter = nb.financial_quarter;

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
