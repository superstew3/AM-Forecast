-- Income becomes the primary associate's share, not the gross brokerage figure.
--
-- The brokerage is the primary associate on these policies, so the money it
-- actually receives is the associate amount, not total commission and fees.
-- Reporting the gross figure overstates income by roughly 7%, and every budget,
-- achievement and bonus figure derived from it was overstated by the same
-- proportion.
--
--   Sales transactions   income = PrimaryAssocAmount
--                        Already GST inclusive; there is no tax column on this
--                        report and none is needed.
--
--   Renewals pending     income = PrimaryAssocCommSum + PrimaryAssocCommTaxSum
--                        The first is GST exclusive, the second is its GST, so
--                        both are required to stay consistent with the sales
--                        side. Verified on the sample: the tax column is 9.992%
--                        of the sum, which is GST net of rounding.
--
-- Commission, fees and their taxes are retained on every row. They are still
-- the gross figures, still auditable, and still what reconciles against the
-- source report — they simply no longer drive reported income. Dropping them
-- would make the change irreversible and unauditable.

-- --------------------------------------------------------------------------
-- Sales
-- --------------------------------------------------------------------------
--
-- Thirty-three views read these columns. They are dropped with CASCADE and
-- recreated verbatim at the end of this file: none of them names commission or
-- fees directly, so every one picks up the new basis without being rewritten.
-- That is the payoff of holding the definition in one generated column.

-- primary_assoc_amount was already captured but never used. It must not be
-- null now that income depends on it.
UPDATE sales_transaction SET primary_assoc_amount = 0 WHERE primary_assoc_amount IS NULL;

ALTER TABLE sales_transaction
    ALTER COLUMN primary_assoc_amount SET DEFAULT 0,
    ALTER COLUMN primary_assoc_amount SET NOT NULL;

ALTER TABLE sales_transaction
    DROP COLUMN actual_income CASCADE,
    DROP COLUMN positive_income CASCADE,
    DROP COLUMN signed_return_income CASCADE,
    DROP COLUMN absolute_return_income CASCADE;

ALTER TABLE sales_transaction
    ADD COLUMN actual_income numeric(14,2)
        GENERATED ALWAYS AS (primary_assoc_amount) STORED NOT NULL,
    ADD COLUMN positive_income numeric(14,2)
        GENERATED ALWAYS AS (GREATEST(primary_assoc_amount, 0)) STORED NOT NULL,
    ADD COLUMN signed_return_income numeric(14,2)
        GENERATED ALWAYS AS (LEAST(primary_assoc_amount, 0)) STORED NOT NULL,
    ADD COLUMN absolute_return_income numeric(14,2)
        GENERATED ALWAYS AS (ABS(LEAST(primary_assoc_amount, 0))) STORED NOT NULL,
    -- Kept for audit and reconciliation against the source report.
    ADD COLUMN gross_income numeric(14,2)
        GENERATED ALWAYS AS (commission + fees) STORED NOT NULL;

COMMENT ON COLUMN sales_transaction.actual_income IS
    'SIG income: the primary associate amount, GST inclusive. This is what the '
    'brokerage receives, and it drives every reported figure.';
COMMENT ON COLUMN sales_transaction.gross_income IS
    'Commission plus fees: the gross brokerage figure. Retained for audit and '
    'for reconciliation against the source report. Not reported as income.';

-- --------------------------------------------------------------------------
-- Renewals
-- --------------------------------------------------------------------------

ALTER TABLE forecast_policy
    ADD COLUMN IF NOT EXISTS primary_assoc_comm_sum     numeric(14,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS primary_assoc_comm_tax_sum numeric(14,2) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS primary_assoc_abbrev       varchar(60);

ALTER TABLE forecast_policy
    DROP COLUMN raw_expected_income CASCADE,
    DROP COLUMN forecast_contribution CASCADE;

ALTER TABLE forecast_policy
    ADD COLUMN raw_expected_income numeric(14,2)
        GENERATED ALWAYS AS (primary_assoc_comm_sum + primary_assoc_comm_tax_sum)
        STORED NOT NULL,
    ADD COLUMN forecast_contribution numeric(14,2)
        GENERATED ALWAYS AS (GREATEST(primary_assoc_comm_sum
                                      + primary_assoc_comm_tax_sum, 0))
        STORED NOT NULL,
    ADD COLUMN gross_expected_income numeric(14,2)
        GENERATED ALWAYS AS (comm + comm_tax + fee + fee_tax) STORED NOT NULL;

COMMENT ON COLUMN forecast_policy.raw_expected_income IS
    'SIG expected income: primary associate commission plus its GST. The sum '
    'column is GST exclusive, so the tax column is required to keep this '
    'consistent with the GST-inclusive sales figures.';
COMMENT ON COLUMN forecast_policy.gross_expected_income IS
    'Comm + CommTax + Fee + FeeTax: the gross figure. Retained for audit. Not '
    'reported as expected income.';

-- Staging already holds computed income figures rather than raw source
-- columns, so it needs no change here: the import service now computes those
-- figures on the new basis, and the preview follows automatically.


-- --------------------------------------------------------------------------
-- Views
-- --------------------------------------------------------------------------
--
-- The CASCADE above removes only the views that read the changed columns.
-- The rest are dropped here in reverse dependency order and every view is
-- then recreated verbatim, so the set is rebuilt from one known state
-- rather than half-replaced.
--
-- None of these definitions names commission or fees directly: they read
-- actual_income and forecast_contribution, which is why changing the basis
-- in two generated columns changes every reported figure without touching
-- a single view body.

DROP VIEW IF EXISTS v_business_dashboard CASCADE;
DROP VIEW IF EXISTS v_outlook_quarter CASCADE;
DROP VIEW IF EXISTS v_new_business_analysis CASCADE;
DROP VIEW IF EXISTS v_budget_performance_quarter CASCADE;
DROP VIEW IF EXISTS v_renewal_performance_month CASCADE;
DROP VIEW IF EXISTS v_prior_year_comparison CASCADE;
DROP VIEW IF EXISTS v_outlook_month CASCADE;
DROP VIEW IF EXISTS v_forecast_position_month CASCADE;
DROP VIEW IF EXISTS v_budget_quarter CASCADE;
DROP VIEW IF EXISTS v_bonus_quarter CASCADE;
DROP VIEW IF EXISTS v_bonus_month CASCADE;
DROP VIEW IF EXISTS v_renewal_income_month CASCADE;
DROP VIEW IF EXISTS v_monthly_budget CASCADE;
DROP VIEW IF EXISTS v_manager_transfer_detail CASCADE;
DROP VIEW IF EXISTS v_latest_forecast_month CASCADE;
DROP VIEW IF EXISTS v_forecast_movement_summary CASCADE;
DROP VIEW IF EXISTS v_actual_month CASCADE;
DROP VIEW IF EXISTS v_sales_reported CASCADE;
DROP VIEW IF EXISTS v_return_income_analysis CASCADE;
DROP VIEW IF EXISTS v_renewal_outcome_performance CASCADE;
DROP VIEW IF EXISTS v_original_forecast_month CASCADE;
DROP VIEW IF EXISTS v_latest_forecast_policy CASCADE;
DROP VIEW IF EXISTS v_forecast_movement_detail CASCADE;
DROP VIEW IF EXISTS v_allocation_breaches CASCADE;
DROP VIEW IF EXISTS v_snapshot_coverage CASCADE;
DROP VIEW IF EXISTS v_policy_renewal CASCADE;
DROP VIEW IF EXISTS v_match_tier_summary CASCADE;
DROP VIEW IF EXISTS v_match_review_queue CASCADE;
DROP VIEW IF EXISTS v_match_outcome_summary CASCADE;
DROP VIEW IF EXISTS v_match_decision_history CASCADE;
DROP VIEW IF EXISTS v_manager_resolution CASCADE;
DROP VIEW IF EXISTS v_baseline_usable CASCADE;
DROP VIEW IF EXISTS v_allocation_integrity CASCADE;

CREATE VIEW v_allocation_integrity AS
SELECT a.transaction_id,
    t.actual_income AS transaction_income,
    sum(a.allocated_income) AS allocated_total,
    count(*) AS allocation_count,
    count(DISTINCT a.policy_id) AS policies_credited,
    count(*) FILTER (WHERE a.method::text = 'auto'::text) AS auto_allocations,
        CASE
            WHEN t.actual_income >= 0::numeric AND sum(a.allocated_income) > (t.actual_income + 0.001) THEN 'over_allocated'::text
            WHEN t.actual_income < 0::numeric AND sum(a.allocated_income) < (t.actual_income - 0.001) THEN 'over_allocated'::text
            WHEN count(*) FILTER (WHERE a.method::text = 'auto'::text) > 1 THEN 'multiple_auto_allocations'::text
            ELSE 'ok'::text
        END AS status
   FROM match_allocation a
     JOIN sales_transaction t ON t.id = a.transaction_id
  GROUP BY a.transaction_id, t.actual_income;

CREATE VIEW v_baseline_usable AS
SELECT b.forecast_month,
    b.financial_year,
    b.financial_quarter,
    m.canonical_manager,
    b.baseline_status,
    b.baseline_source,
    b.baseline_status::text = 'complete'::text AND NOT b.suppress_achievement AND NOT b.manager_exceptions ? m.canonical_manager::text AS baseline_usable,
    b.note
   FROM forecast_baseline b
     CROSS JOIN reporting_manager m;

CREATE VIEW v_manager_resolution AS
SELECT a.source_manager,
    a.source_manager_norm,
    a.canonical_manager,
    m.status,
    m.include_in_rankings,
    m.include_in_business_totals,
    m.display_order
   FROM manager_alias a
     JOIN reporting_manager m ON m.canonical_manager::text = a.canonical_manager::text
  WHERE a.active;

CREATE VIEW v_match_decision_history AS
SELECT d.id,
    d.decided_at,
    d.reviewer,
    d.action,
    d.reason,
    d.policy_id,
    d.forecast_month,
    d.transaction_id,
    d.previous_decision,
    d.new_decision,
    t.client_code,
    t.policy_number,
    t.category,
    t.actual_income
   FROM match_decision d
     LEFT JOIN sales_transaction t ON t.id = d.transaction_id
  ORDER BY d.decided_at DESC;

CREATE VIEW v_match_outcome_summary AS
SELECT canonical_manager,
    forecast_month,
    outcome,
    count(*) AS policies,
    sum(original_forecast_income) AS original_forecast_income,
    sum(renewal_transaction_income) AS renewal_transaction_income,
    sum(total_associated_income) AS total_associated_income
   FROM policy_outcome po
  GROUP BY canonical_manager, forecast_month, outcome;

CREATE VIEW v_match_review_queue AS
SELECT mc.id,
    mc.reason,
    mc.status,
    mc.tier,
    mc.confidence,
    mc.candidate_rank,
    mc.transaction_id,
    t.client_code AS txn_client,
    t.policy_number AS txn_policy_number,
    t.policy_class AS txn_policy_class,
    t.category AS txn_category,
    t.transaction_date,
    t.actual_income AS txn_income,
    mc.policy_id,
    fp.client_code AS policy_client,
    fp.policy_number AS policy_policy_number,
    fp.class_abbrev AS policy_class,
    fp.expiry_date,
    fp.forecast_contribution,
    mc.detail,
    mc.created_at
   FROM match_candidate mc
     LEFT JOIN sales_transaction t ON t.id = mc.transaction_id
     LEFT JOIN LATERAL ( SELECT p.client_code,
            p.policy_number,
            p.class_abbrev,
            p.expiry_date,
            p.forecast_contribution
           FROM forecast_policy p
          WHERE p.policy_id = mc.policy_id
          ORDER BY p.snapshot_id DESC
         LIMIT 1) fp ON true;

CREATE VIEW v_match_tier_summary AS
SELECT tier,
        CASE tier
            WHEN 1 THEN 'client + policy number + compatible class + date'::text
            WHEN 2 THEN 'client + policy number + date'::text
            WHEN 3 THEN 'client + policy number, same financial year'::text
            WHEN 4 THEN 'client + compatible class + date'::text
            ELSE NULL::text
        END AS tier_description,
    method,
    count(*) AS allocations,
    count(DISTINCT policy_id) AS policies,
    count(DISTINCT transaction_id) AS transactions,
    sum(allocated_income) AS allocated_income,
    sum(allocated_income) FILTER (WHERE is_renewal_income) AS renewal_income,
    min(confidence) AS min_confidence,
    max(confidence) AS max_confidence
   FROM match_allocation a
  GROUP BY tier, method;

CREATE VIEW v_policy_renewal AS
SELECT po.policy_id,
    po.forecast_month,
    po.canonical_manager,
    lp.source_manager AS original_manager,
    lp.client_code,
    lp.policy_number,
    lp.class_abbrev,
    lp.underwriter_abbrev,
    lp.expiry_date,
    po.original_forecast_income,
    po.latest_forecast_income,
    po.latest_forecast_income - po.original_forecast_income AS forecast_movement,
    po.outcome,
    po.renewal_transaction_income,
    po.total_associated_income,
    po.matched_transaction_count,
    po.best_tier,
    po.confidence,
    po.requires_review,
    po.is_manual,
    lp.exception_flags,
    lp.snapshot_id AS source_snapshot
   FROM policy_outcome po
     LEFT JOIN LATERAL ( SELECT fp.client_code,
            fp.policy_number,
            fp.class_abbrev,
            fp.underwriter_abbrev,
            fp.expiry_date,
            fp.source_manager,
            fp.exception_flags,
            fp.snapshot_id
           FROM forecast_policy fp
          WHERE fp.policy_id = po.policy_id AND fp.forecast_month = po.forecast_month
          ORDER BY fp.snapshot_id DESC
         LIMIT 1) lp ON true;

CREATE VIEW v_snapshot_coverage AS
SELECT smc.snapshot_id,
    s.as_of_date,
    b.file_name,
    smc.forecast_month,
    smc.policy_count,
    smc.forecast_contribution,
    smc.is_confirmed_complete,
    smc.coverage_basis,
    c.latest_snapshot_id = smc.snapshot_id AS is_current_latest
   FROM snapshot_month_coverage smc
     JOIN forecast_snapshot s ON s.id = smc.snapshot_id
     JOIN upload_batch b ON b.id = s.batch_id
     LEFT JOIN forecast_month_coverage c ON c.forecast_month = smc.forecast_month;

CREATE VIEW v_allocation_breaches AS
SELECT transaction_id,
    transaction_income,
    allocated_total,
    allocation_count,
    policies_credited,
    auto_allocations,
    status
   FROM v_allocation_integrity
  WHERE status <> 'ok'::text;

CREATE VIEW v_forecast_movement_detail AS
SELECT m.id,
    m.from_snapshot_id,
    m.to_snapshot_id,
    m.policy_id,
    m.forecast_month,
    m.movement_type,
    m.original_income,
    m.previous_income,
    m.latest_income,
    m.movement_amount,
    m.from_manager,
    m.to_manager,
    m.detail_changes,
    m.detected_at,
    m.added,
    m.removed,
    m.amount_changed,
    m.manager_changed,
    m.detail_changed,
    m.secondary_changes,
    COALESCE(rt.canonical_manager, m.to_manager) AS canonical_to_manager,
    COALESCE(rf.canonical_manager, m.from_manager) AS canonical_from_manager,
    p.client_code,
    p.policy_number,
    p.class_abbrev,
    p.underwriter_abbrev,
    p.expiry_date
   FROM forecast_movement m
     LEFT JOIN v_manager_resolution rt ON rt.source_manager::text = m.to_manager::text
     LEFT JOIN v_manager_resolution rf ON rf.source_manager::text = m.from_manager::text
     LEFT JOIN LATERAL ( SELECT fp.client_code,
            fp.policy_number,
            fp.class_abbrev,
            fp.underwriter_abbrev,
            fp.expiry_date
           FROM forecast_policy fp
          WHERE fp.policy_id = m.policy_id
          ORDER BY fp.snapshot_id DESC
         LIMIT 1) p ON true;

CREATE VIEW v_latest_forecast_policy AS
SELECT p.id,
    p.snapshot_id,
    p.policy_id,
    p.client_id,
    p.client_code,
    p.client_code_norm,
    p.policy_number,
    p.policy_number_norm,
    p.class_abbrev,
    p.class_code,
    p.class_description,
    p.underwriter_abbrev,
    p.inception_date,
    p.expiry_date,
    p.next_expiry_date,
    p.renewal_months,
    p.forecast_month,
    p.financial_year,
    p.financial_quarter,
    p.source_manager,
    p.comm,
    p.comm_tax,
    p.fee,
    p.fee_tax,
    p.premium,
    p.total_premium,
    p.raw_expected_income,
    p.forecast_contribution,
    p.exception_flags,
    p.is_excluded,
    p.exclusion_rule_id,
    p.exclusion_field,
    p.exclusion_value,
    p.source_row,
    c.latest_snapshot_id,
    COALESCE(r.canonical_manager, p.source_manager) AS canonical_manager
   FROM forecast_month_coverage c
     JOIN forecast_policy p ON p.snapshot_id = c.latest_snapshot_id AND p.forecast_month = c.forecast_month
     LEFT JOIN v_manager_resolution r ON r.source_manager::text = p.source_manager::text
  WHERE NOT p.is_excluded;

CREATE VIEW v_original_forecast_month AS
SELECT COALESCE(r.canonical_manager, o.source_manager) AS canonical_manager,
    o.forecast_month,
    o.financial_year,
    o.financial_quarter,
    o.grain,
    o.origin,
    sum(o.forecast_contribution) AS original_forecast,
    count(*) FILTER (WHERE o.grain::text = 'policy'::text) AS original_policy_count
   FROM original_forecast o
     LEFT JOIN v_manager_resolution r ON r.source_manager::text = o.source_manager::text
  GROUP BY (COALESCE(r.canonical_manager, o.source_manager)), o.forecast_month, o.financial_year, o.financial_quarter, o.grain, o.origin;

CREATE VIEW v_renewal_outcome_performance AS
WITH agg AS (
         SELECT policy_outcome.canonical_manager,
            policy_outcome.forecast_month,
            sum(policy_outcome.original_forecast_income) AS original_forecast,
            sum(policy_outcome.renewal_transaction_income) AS actual_renewal_income,
            sum(policy_outcome.total_associated_income) AS total_associated_income,
            count(*) AS original_policies,
            count(*) FILTER (WHERE policy_outcome.outcome::text = 'renewed'::text) AS policies_renewed,
            count(*) FILTER (WHERE policy_outcome.outcome::text = 'transfer_renewed'::text) AS policies_transferred,
            count(*) FILTER (WHERE policy_outcome.outcome::text = 'lapsed_lost'::text) AS policies_lapsed,
            count(*) FILTER (WHERE policy_outcome.outcome::text = 'pending'::text) AS policies_pending,
            count(*) FILTER (WHERE policy_outcome.outcome::text = 'removed_from_latest'::text) AS policies_removed,
            count(*) FILTER (WHERE policy_outcome.outcome::text = ANY (ARRAY['multiple_candidates'::character varying, 'unmatched'::character varying]::text[])) AS policies_unresolved,
            sum(policy_outcome.original_forecast_income) FILTER (WHERE policy_outcome.outcome::text = ANY (ARRAY['renewed'::character varying, 'transfer_renewed'::character varying]::text[])) AS retained_forecast_income
           FROM policy_outcome
          GROUP BY policy_outcome.canonical_manager, policy_outcome.forecast_month
        )
 SELECT a.canonical_manager,
    a.forecast_month,
    a.original_forecast,
    a.actual_renewal_income,
    a.total_associated_income,
    a.original_policies,
    a.policies_renewed,
    a.policies_transferred,
    a.policies_lapsed,
    a.policies_pending,
    a.policies_removed,
    a.policies_unresolved,
    a.retained_forecast_income,
    u.baseline_usable,
        CASE
            WHEN u.baseline_usable THEN a.actual_renewal_income - a.original_forecast
            ELSE NULL::numeric
        END AS renewal_variance,
        CASE
            WHEN u.baseline_usable THEN safe_div(a.actual_renewal_income, a.original_forecast)
            ELSE NULL::numeric
        END AS renewal_achievement,
    safe_div((a.policies_renewed + a.policies_transferred)::numeric, NULLIF(a.original_policies - a.policies_pending, 0)::numeric) AS retention_by_policy_count,
    safe_div(a.retained_forecast_income, NULLIF(a.original_forecast, 0::numeric)) AS retention_by_income
   FROM agg a
     LEFT JOIN v_baseline_usable u ON u.canonical_manager::text = a.canonical_manager::text AND u.forecast_month = a.forecast_month;

CREATE VIEW v_return_income_analysis AS
SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
    t.financial_year,
    t.financial_quarter,
    t.period_month,
    t.derived_classification,
    sum(t.signed_return_income) AS signed_return_income,
    sum(t.absolute_return_income) AS absolute_return_income,
    count(*) AS transaction_rows
   FROM sales_transaction t
     LEFT JOIN v_manager_resolution r ON r.source_manager::text = t.source_manager::text
  WHERE NOT t.is_excluded AND t.actual_income < 0::numeric
  GROUP BY (COALESCE(r.canonical_manager, t.source_manager)), t.financial_year, t.financial_quarter, t.period_month, t.derived_classification;

CREATE VIEW v_sales_reported AS
SELECT t.id,
    t.fingerprint,
    t.first_seen_batch_id,
    t.first_seen_at,
    t.last_seen_batch_id,
    t.last_seen_at,
    t.seen_count,
    t.transaction_date,
    t.period_month,
    t.financial_year,
    t.financial_quarter,
    t.source_manager,
    t.group1_id,
    t.group2_description,
    t.client_id,
    t.client_code,
    t.client_code_norm,
    t.policy_number,
    t.policy_number_norm,
    t.invoice_number,
    t.username,
    t.category,
    t.business_classification,
    t.derived_classification,
    t.policy_class,
    t.uw_code,
    t.reason,
    t.premium,
    t.nett,
    t.commission,
    t.fees,
    t.sub_comm,
    t.actual_income,
    t.positive_income,
    t.signed_return_income,
    t.absolute_return_income,
    t.financial_direction,
    t.primary_assoc_code,
    t.primary_assoc_amount,
    t.secondary_assoc_code,
    t.secondary_assoc_amount,
    t.is_excluded,
    t.exclusion_rule_id,
    t.exclusion_field,
    t.exclusion_value,
    t.source_row,
    r.canonical_manager,
    r.include_in_rankings,
    r.include_in_business_totals
   FROM sales_transaction t
     LEFT JOIN v_manager_resolution r ON r.source_manager::text = t.source_manager::text
  WHERE NOT t.is_excluded;

CREATE VIEW v_actual_month AS
SELECT COALESCE(canonical_manager, source_manager) AS canonical_manager,
    period_month,
    financial_year,
    financial_quarter,
    sum(positive_income) AS positive_actual_income,
    sum(signed_return_income) AS signed_return_income,
    sum(absolute_return_income) AS absolute_return_income,
    sum(actual_income) AS net_actual_income,
    sum(actual_income) FILTER (WHERE category::text = ANY (ARRAY['RWL'::character varying, 'TRW'::character varying]::text[])) AS actual_renewal_income,
    sum(actual_income) FILTER (WHERE category::text = 'N/B'::text) AS actual_new_business,
    sum(absolute_return_income) FILTER (WHERE category::text = 'NCN'::text) AS new_business_cancellation,
    sum(absolute_return_income) FILTER (WHERE category::text = 'LAP'::text) AS lapse_income_returned,
    sum(absolute_return_income) FILTER (WHERE category::text = 'MCN'::text) AS midterm_cancellation_returned,
    sum(actual_income) FILTER (WHERE category::text = 'END'::text AND actual_income > 0::numeric) AS positive_endorsements,
    sum(absolute_return_income) FILTER (WHERE category::text = 'END'::text AND actual_income < 0::numeric) AS negative_endorsements,
    sum(absolute_return_income) FILTER (WHERE category::text = 'ECN'::text) AS endorsement_cancellations,
    count(*) AS transaction_rows
   FROM v_sales_reported
  GROUP BY (COALESCE(canonical_manager, source_manager)), period_month, financial_year, financial_quarter;

CREATE VIEW v_forecast_movement_summary AS
SELECT forecast_month,
    COALESCE(canonical_from_manager, canonical_to_manager) AS canonical_manager,
    sum(original_income) AS original_expected_income,
    count(*) FILTER (WHERE removed) AS policies_removed,
    COALESCE(sum(previous_income) FILTER (WHERE removed), 0::numeric) AS expected_income_removed,
    count(*) FILTER (WHERE added) AS policies_added,
    COALESCE(sum(latest_income) FILTER (WHERE added), 0::numeric) AS expected_income_added,
    COALESCE(sum(movement_amount) FILTER (WHERE amount_changed), 0::numeric) AS amount_changes,
    count(*) FILTER (WHERE amount_changed) AS policies_amount_changed,
    count(*) FILTER (WHERE manager_changed) AS manager_transfers,
    count(*) FILTER (WHERE detail_changed) AS detail_changes,
    count(*) FILTER (WHERE cardinality(secondary_changes) > 1) AS multi_attribute_changes,
    sum(latest_income) AS latest_expected_income
   FROM v_forecast_movement_detail
  GROUP BY forecast_month, (COALESCE(canonical_from_manager, canonical_to_manager));

CREATE VIEW v_latest_forecast_month AS
SELECT canonical_manager,
    forecast_month,
    financial_year,
    financial_quarter,
    sum(forecast_contribution) AS latest_forecast,
    sum(raw_expected_income) AS latest_raw_expected,
    count(*) AS policy_count,
    count(*) FILTER (WHERE cardinality(exception_flags) > 0) AS exception_policies
   FROM v_latest_forecast_policy
  GROUP BY canonical_manager, forecast_month, financial_year, financial_quarter;

CREATE VIEW v_manager_transfer_detail AS
SELECT policy_id,
    forecast_month,
    canonical_from_manager,
    canonical_to_manager,
    from_manager AS source_from_manager,
    to_manager AS source_to_manager,
    previous_income,
    latest_income,
    movement_amount,
    amount_changed,
    detail_changed,
    secondary_changes,
    movement_type AS primary_movement_type,
    client_code,
    policy_number,
    class_abbrev,
    expiry_date
   FROM v_forecast_movement_detail
  WHERE manager_changed;

CREATE VIEW v_monthly_budget AS
WITH monthly AS (
         SELECT v_original_forecast_month.canonical_manager,
            v_original_forecast_month.forecast_month,
            v_original_forecast_month.financial_year,
            v_original_forecast_month.financial_quarter,
            sum(v_original_forecast_month.original_forecast) AS original_forecast
           FROM v_original_forecast_month
          GROUP BY v_original_forecast_month.canonical_manager, v_original_forecast_month.forecast_month, v_original_forecast_month.financial_year, v_original_forecast_month.financial_quarter
        ), quarterly AS (
         SELECT monthly.canonical_manager,
            monthly.financial_year,
            monthly.financial_quarter,
            sum(monthly.original_forecast) AS quarter_original,
            count(*) AS months_in_quarter
           FROM monthly
          GROUP BY monthly.canonical_manager, monthly.financial_year, monthly.financial_quarter
        ), resolved AS (
         SELECT m.canonical_manager,
            m.forecast_month,
            m.financial_year,
            m.financial_quarter,
            m.original_forecast,
            q.quarter_original,
            q.months_in_quarter,
            g.basis AS growth_basis,
            g.growth_pct,
            g.dollar_override
           FROM monthly m
             JOIN quarterly q ON q.canonical_manager::text = m.canonical_manager::text AND q.financial_year = m.financial_year AND q.financial_quarter = m.financial_quarter
             CROSS JOIN LATERAL resolve_growth_month(m.canonical_manager::text, m.forecast_month) g(basis, growth_pct, dollar_override, note)
        ), calculated AS (
         SELECT r.canonical_manager,
            r.forecast_month,
            r.financial_year,
            r.financial_quarter,
            r.original_forecast,
            r.quarter_original,
            r.months_in_quarter,
            r.growth_basis,
            r.growth_pct,
            r.dollar_override,
                CASE
                    WHEN r.dollar_override IS NOT NULL AND r.growth_basis = 'manager_month'::text THEN r.dollar_override
                    WHEN r.dollar_override IS NOT NULL AND r.quarter_original > 0::numeric THEN r.dollar_override * (r.original_forecast / r.quarter_original)
                    WHEN r.dollar_override IS NOT NULL THEN r.dollar_override / NULLIF(r.months_in_quarter, 0)::numeric
                    ELSE r.original_forecast * r.growth_pct
                END AS calculated_growth_target,
                CASE
                    WHEN r.dollar_override IS NOT NULL THEN 'dollar_override'::text
                    ELSE 'growth_percentage'::text
                END AS allocation_method
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
    l.locked_budget IS NOT NULL AS is_locked,
    l.locked_at,
    l.locked_by,
    l.reason AS lock_reason,
    COALESCE(o.override_amount, c.calculated_growth_target) AS new_business_growth_target,
    o.override_amount IS NOT NULL AS is_overridden,
    o.reason AS override_reason,
    COALESCE(l.locked_budget, c.original_forecast + COALESCE(o.override_amount, c.calculated_growth_target)) AS total_budget
   FROM calculated c
     LEFT JOIN monthly_target_override o ON o.canonical_manager::text = c.canonical_manager::text AND o.target_month = c.forecast_month AND o.active
     LEFT JOIN budget_lock l ON l.canonical_manager::text = c.canonical_manager::text AND l.target_month = c.forecast_month AND l.active;

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
          WHERE NOT t.is_excluded AND (t.category::text = ANY (ARRAY['RWL'::character varying, 'TRW'::character varying]::text[]))
          GROUP BY (COALESCE(r.canonical_manager, t.source_manager)), t.period_month, t.financial_year, t.financial_quarter
        ), forecast AS (
         SELECT v_original_forecast_month.canonical_manager,
            v_original_forecast_month.forecast_month,
            sum(v_original_forecast_month.original_forecast) AS original_forecast
           FROM v_original_forecast_month
          GROUP BY v_original_forecast_month.canonical_manager, v_original_forecast_month.forecast_month
        ), cut AS (
         SELECT date_trunc('month'::text, reporting_settings.cut_off_date::timestamp with time zone)::date AS cut_month
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

CREATE VIEW v_bonus_month AS
WITH settings AS (
         SELECT reporting_settings.bonus_base_divisor AS divisor,
            reporting_settings.bonus_above_target_rate AS above_rate,
            date_trunc('month'::text, reporting_settings.cut_off_date::timestamp with time zone)::date AS cut_month
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
        END, 2) AS indicative_bonus
   FROM v_monthly_budget b
     CROSS JOIN settings s
     LEFT JOIN v_actual_month a ON a.canonical_manager::text = b.canonical_manager::text AND a.period_month = b.forecast_month;

CREATE VIEW v_bonus_quarter AS
WITH settings AS (
         SELECT reporting_settings.bonus_base_divisor AS divisor,
            reporting_settings.bonus_above_target_rate AS above_rate,
            date_trunc('month'::text, reporting_settings.cut_off_date::timestamp with time zone)::date AS cut_month
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
    round(
        CASE
            WHEN b.months_elapsed = 0 THEN NULL::numeric
            WHEN COALESCE(a.actual_income, 0::numeric) < b.budget_target THEN 0::numeric
            ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric)
        END, 2) AS base_bonus,
    round(
        CASE
            WHEN b.months_elapsed = 0 THEN NULL::numeric
            WHEN COALESCE(a.actual_income, 0::numeric) < b.budget_target THEN 0::numeric
            ELSE (COALESCE(a.actual_income, 0::numeric) - b.budget_target) * s.above_rate
        END, 2) AS above_target_bonus,
    round(
        CASE
            WHEN b.months_elapsed = 0 THEN NULL::numeric
            WHEN COALESCE(a.actual_income, 0::numeric) < b.budget_target THEN 0::numeric
            ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric) + (COALESCE(a.actual_income, 0::numeric) - b.budget_target) * s.above_rate
        END, 2) AS total_bonus,
    round((b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric), 2) AS bonus_at_target,
    round(GREATEST(b.budget_target - COALESCE(a.actual_income, 0::numeric), 0::numeric), 2) AS income_still_required,
    round(
        CASE
            WHEN b.months_elapsed > 0 AND b.months_elapsed < b.months_in_quarter THEN COALESCE(a.actual_income, 0::numeric) * (b.months_in_quarter::numeric / b.months_elapsed::numeric)
            ELSE NULL::numeric
        END, 2) AS projected_income,
    round(
        CASE
            WHEN b.months_elapsed = 0 OR b.months_elapsed >= b.months_in_quarter THEN NULL::numeric
            WHEN (COALESCE(a.actual_income, 0::numeric) * (b.months_in_quarter::numeric / b.months_elapsed::numeric)) < b.budget_target THEN 0::numeric
            ELSE (b.budget_target - b.expected_income) / NULLIF(s.divisor, 0::numeric) + (COALESCE(a.actual_income, 0::numeric) * (b.months_in_quarter::numeric / b.months_elapsed::numeric) - b.budget_target) * s.above_rate
        END, 2) AS projected_bonus,
    s.divisor AS bonus_base_divisor,
    s.above_rate AS bonus_above_target_rate
   FROM budget b
     CROSS JOIN settings s
     LEFT JOIN actual a ON a.canonical_manager::text = b.canonical_manager::text AND a.financial_year = b.financial_year AND a.financial_quarter = b.financial_quarter;

CREATE VIEW v_budget_quarter AS
SELECT canonical_manager,
    financial_year,
    financial_quarter,
    sum(original_forecast) AS original_renewal_forecast,
        CASE
            WHEN count(DISTINCT growth_basis) = 1 THEN min(growth_basis)
            ELSE 'mixed'::text
        END AS growth_basis,
        CASE
            WHEN count(DISTINCT growth_pct) = 1 THEN min(growth_pct)
            ELSE NULL::numeric
        END AS growth_pct,
    NULLIF(sum(override_amount), 0::numeric) AS dollar_override,
    sum(new_business_growth_target) AS new_business_growth_target,
    sum(total_budget) AS total_budget,
    bool_or(is_locked) AS has_locked_months,
    count(*) FILTER (WHERE is_locked) AS locked_months
   FROM v_monthly_budget
  GROUP BY canonical_manager, financial_year, financial_quarter;

CREATE VIEW v_forecast_position_month AS
WITH cut AS (
         SELECT date_trunc('month'::text, reporting_settings.cut_off_date::timestamp with time zone)::date AS cut_month
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

CREATE VIEW v_outlook_month AS
WITH cut AS (
         SELECT date_trunc('month'::text, reporting_settings.cut_off_date::timestamp with time zone)::date AS cut_month
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
            NULL::numeric,
            v_latest_forecast_month.latest_forecast,
            'forecast'::text
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

CREATE VIEW v_prior_year_comparison AS
SELECT canonical_manager,
    financial_year + 1 AS comparison_financial_year,
    financial_year AS prior_financial_year,
    sum(net_actual_income) AS prior_year_net_actual_income,
    sum(positive_actual_income) AS prior_year_positive_income,
    sum(absolute_return_income) AS prior_year_return_income,
    sum(actual_renewal_income) AS prior_year_renewal_income,
    sum(actual_new_business) AS prior_year_new_business
   FROM v_actual_month
  GROUP BY canonical_manager, financial_year;

CREATE VIEW v_renewal_performance_month AS
SELECT a.canonical_manager,
    a.period_month,
    a.financial_year,
    a.financial_quarter,
    a.actual_renewal_income,
    o.original_forecast,
    u.baseline_usable,
    u.baseline_source,
        CASE
            WHEN u.baseline_usable THEN a.actual_renewal_income - o.original_forecast
            ELSE NULL::numeric
        END AS renewal_variance,
        CASE
            WHEN u.baseline_usable THEN safe_div(a.actual_renewal_income, o.original_forecast)
            ELSE NULL::numeric
        END AS renewal_achievement
   FROM v_actual_month a
     LEFT JOIN v_original_forecast_month o ON o.canonical_manager::text = a.canonical_manager::text AND o.forecast_month = a.period_month
     LEFT JOIN v_baseline_usable u ON u.canonical_manager::text = a.canonical_manager::text AND u.forecast_month = a.period_month;

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
            sum(t.actual_income) FILTER (WHERE t.category::text = ANY (ARRAY['N/B'::character varying::text, 'NCN'::character varying::text])) AS net_new_business
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
