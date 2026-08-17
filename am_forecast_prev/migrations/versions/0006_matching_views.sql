-- Stage 4 reporting. All amounts GST inclusive.

-- ---------------------------------------------------------------------------
-- Policy-level renewal view (reporting view F)
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_policy_renewal AS
SELECT po.policy_id,
       po.forecast_month,
       po.canonical_manager,
       lp.source_manager           AS original_manager,
       lp.client_code,
       lp.policy_number,
       lp.class_abbrev,
       lp.underwriter_abbrev,
       lp.expiry_date,
       po.original_forecast_income,
       po.latest_forecast_income,
       po.latest_forecast_income - po.original_forecast_income AS forecast_movement,
       po.outcome,
       -- The two income measures stay separate. Renewal achievement uses the
       -- first. The second answers "what did this policy generate in total",
       -- which is a different question.
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
LEFT JOIN LATERAL (
    SELECT client_code, policy_number, class_abbrev, underwriter_abbrev,
           expiry_date, source_manager, exception_flags, snapshot_id
    FROM forecast_policy fp
    WHERE fp.policy_id = po.policy_id AND fp.forecast_month = po.forecast_month
    ORDER BY fp.snapshot_id DESC LIMIT 1
) lp ON true;

-- ---------------------------------------------------------------------------
-- Match run summary and tier breakdown
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_match_tier_summary AS
SELECT a.tier,
       CASE a.tier
         WHEN 1 THEN 'client + policy number + compatible class + date'
         WHEN 2 THEN 'client + policy number + date'
         WHEN 3 THEN 'client + policy number, same financial year'
         WHEN 4 THEN 'client + compatible class + date'
       END AS tier_description,
       a.method,
       count(*)                                                   AS allocations,
       count(DISTINCT a.policy_id)                                AS policies,
       count(DISTINCT a.transaction_id)                           AS transactions,
       SUM(a.allocated_income)                                    AS allocated_income,
       SUM(a.allocated_income) FILTER (WHERE a.is_renewal_income) AS renewal_income,
       MIN(a.confidence)                                          AS min_confidence,
       MAX(a.confidence)                                          AS max_confidence
FROM match_allocation a
GROUP BY a.tier, a.method;

CREATE OR REPLACE VIEW v_match_outcome_summary AS
SELECT po.canonical_manager,
       po.forecast_month,
       po.outcome,
       count(*)                              AS policies,
       SUM(po.original_forecast_income)      AS original_forecast_income,
       SUM(po.renewal_transaction_income)    AS renewal_transaction_income,
       SUM(po.total_associated_income)       AS total_associated_income
FROM policy_outcome po
GROUP BY 1, 2, 3;

-- ---------------------------------------------------------------------------
-- Review queue
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_match_review_queue AS
SELECT mc.id,
       mc.reason,
       mc.status,
       mc.tier,
       mc.confidence,
       mc.candidate_rank,
       mc.transaction_id,
       t.client_code      AS txn_client,
       t.policy_number    AS txn_policy_number,
       t.policy_class     AS txn_policy_class,
       t.category         AS txn_category,
       t.transaction_date,
       t.actual_income    AS txn_income,
       mc.policy_id,
       fp.client_code     AS policy_client,
       fp.policy_number   AS policy_policy_number,
       fp.class_abbrev    AS policy_class,
       fp.expiry_date,
       fp.forecast_contribution,
       mc.detail,
       mc.created_at
FROM match_candidate mc
LEFT JOIN sales_transaction t ON t.id = mc.transaction_id
LEFT JOIN LATERAL (
    SELECT client_code, policy_number, class_abbrev, expiry_date, forecast_contribution
    FROM forecast_policy p
    WHERE p.policy_id = mc.policy_id ORDER BY p.snapshot_id DESC LIMIT 1
) fp ON true;

-- ---------------------------------------------------------------------------
-- Duplicate-allocation control
--
-- Should always return zero rows. The trigger on match_allocation prevents the
-- condition; this view is the standing check that it did.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_allocation_integrity AS
SELECT a.transaction_id,
       t.actual_income                AS transaction_income,
       SUM(a.allocated_income)        AS allocated_total,
       count(*)                       AS allocation_count,
       count(DISTINCT a.policy_id)    AS policies_credited,
       count(*) FILTER (WHERE a.method = 'auto') AS auto_allocations,
       CASE
         WHEN t.actual_income >= 0 AND SUM(a.allocated_income) > t.actual_income + 0.001
           THEN 'over_allocated'
         WHEN t.actual_income < 0 AND SUM(a.allocated_income) < t.actual_income - 0.001
           THEN 'over_allocated'
         WHEN count(*) FILTER (WHERE a.method = 'auto') > 1
           THEN 'multiple_auto_allocations'
         ELSE 'ok'
       END AS status
FROM match_allocation a
JOIN sales_transaction t ON t.id = a.transaction_id
GROUP BY a.transaction_id, t.actual_income;

CREATE OR REPLACE VIEW v_allocation_breaches AS
SELECT * FROM v_allocation_integrity WHERE status <> 'ok';

-- ---------------------------------------------------------------------------
-- Renewal performance: forecast against actual RWL/TRW
--
-- Renewal achievement uses renewal_transaction_income only, so a lapse reduces
-- achievement by contributing nothing, not by contributing a negative. The
-- lapse's own negative transaction still reduces Net Actual Income and appears
-- in Return Income elsewhere.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_renewal_outcome_performance AS
WITH agg AS (
    SELECT canonical_manager, forecast_month,
           SUM(original_forecast_income)   AS original_forecast,
           SUM(renewal_transaction_income) AS actual_renewal_income,
           SUM(total_associated_income)    AS total_associated_income,
           count(*)                                                   AS original_policies,
           count(*) FILTER (WHERE outcome = 'renewed')                AS policies_renewed,
           count(*) FILTER (WHERE outcome = 'transfer_renewed')       AS policies_transferred,
           count(*) FILTER (WHERE outcome = 'lapsed_lost')            AS policies_lapsed,
           count(*) FILTER (WHERE outcome = 'pending')                AS policies_pending,
           count(*) FILTER (WHERE outcome = 'removed_from_latest')    AS policies_removed,
           count(*) FILTER (WHERE outcome IN ('multiple_candidates', 'unmatched'))
                                                                      AS policies_unresolved,
           SUM(original_forecast_income) FILTER
               (WHERE outcome IN ('renewed', 'transfer_renewed'))     AS retained_forecast_income
    FROM policy_outcome
    GROUP BY 1, 2
)
SELECT a.*,
       u.baseline_usable,
       CASE WHEN u.baseline_usable
            THEN a.actual_renewal_income - a.original_forecast END AS renewal_variance,
       CASE WHEN u.baseline_usable
            THEN safe_div(a.actual_renewal_income, a.original_forecast) END
                                                                   AS renewal_achievement,
       safe_div((a.policies_renewed + a.policies_transferred)::numeric,
                NULLIF(a.original_policies - a.policies_pending, 0))
                                                                   AS retention_by_policy_count,
       safe_div(a.retained_forecast_income, NULLIF(a.original_forecast, 0))
                                                                   AS retention_by_income
FROM agg a
LEFT JOIN v_baseline_usable u
       ON u.canonical_manager = a.canonical_manager
      AND u.forecast_month = a.forecast_month;

-- ---------------------------------------------------------------------------
-- Manual decision audit
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_match_decision_history AS
SELECT d.id, d.decided_at, d.reviewer, d.action, d.reason,
       d.policy_id, d.forecast_month, d.transaction_id,
       d.previous_decision, d.new_decision,
       t.client_code, t.policy_number, t.category, t.actual_income
FROM match_decision d
LEFT JOIN sales_transaction t ON t.id = d.transaction_id
ORDER BY d.decided_at DESC;

-- ---------------------------------------------------------------------------
-- Snapshot coverage reporting
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW v_snapshot_coverage AS
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
