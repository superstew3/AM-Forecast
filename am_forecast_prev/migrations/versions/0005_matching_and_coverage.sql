-- Stage 4 schema.
--   1. Movement gains independent flags: a policy can change manager and amount
--      in the same snapshot, and both must be countable.
--   2. Snapshot month coverage: a month absent from a narrower export is not a
--      mass removal unless the upload confirms it covered that month.
--   3. Class equivalence: the two sources use different class vocabularies, so
--      compatibility is a mapping, not string equality.
--   4. Matching: policy outcome and income allocation are separate concerns.

-- ---------------------------------------------------------------------------
-- 1. Multi-attribute movement
-- ---------------------------------------------------------------------------

ALTER TABLE forecast_movement
    ADD COLUMN added           boolean NOT NULL DEFAULT false,
    ADD COLUMN removed         boolean NOT NULL DEFAULT false,
    ADD COLUMN amount_changed  boolean NOT NULL DEFAULT false,
    ADD COLUMN manager_changed boolean NOT NULL DEFAULT false,
    ADD COLUMN detail_changed  boolean NOT NULL DEFAULT false,
    ADD COLUMN secondary_changes varchar(30)[] NOT NULL DEFAULT '{}'::varchar[];

COMMENT ON COLUMN forecast_movement.movement_type IS
  'Primary classification for display. Use the boolean flags for counting: a '
  'policy that changed manager AND amount is movement_type=amount_changed but '
  'manager_changed is also true, and a manager-transfer count that reads only '
  'movement_type would miss it.';

CREATE INDEX ix_movement_manager_changed ON forecast_movement (forecast_month)
    WHERE manager_changed;
CREATE INDEX ix_movement_detail_changed ON forecast_movement (forecast_month)
    WHERE detail_changed;

-- ---------------------------------------------------------------------------
-- 2. Snapshot coverage confirmation
-- ---------------------------------------------------------------------------

CREATE TABLE snapshot_month_coverage (
    id                    bigserial PRIMARY KEY,
    snapshot_id           bigint NOT NULL REFERENCES forecast_snapshot(id) ON DELETE CASCADE,
    forecast_month        date NOT NULL,
    policy_count          integer NOT NULL,
    forecast_contribution numeric(14,2) NOT NULL,
    -- A month is 'complete' only when the upload is confirmed to cover it in
    -- full. Absence of a month from a newer file is otherwise treated as
    -- "not reported", not "everything lapsed".
    is_confirmed_complete boolean NOT NULL DEFAULT false,
    coverage_basis        text NOT NULL DEFAULT 'observed'
        CHECK (coverage_basis IN ('observed', 'confirmed_by_user', 'declared_by_file')),
    UNIQUE (snapshot_id, forecast_month)
);

CREATE INDEX ix_snapshot_month_coverage_month ON snapshot_month_coverage (forecast_month);

ALTER TABLE upload_batch
    ADD COLUMN requires_confirmation boolean NOT NULL DEFAULT false,
    ADD COLUMN confirmation_note     text,
    ADD COLUMN confirmed_by          varchar(120),
    ADD COLUMN confirmed_at          timestamptz,
    ADD COLUMN confirmed_months      date[] NOT NULL DEFAULT '{}'::date[],
    ADD COLUMN coverage_warnings     jsonb NOT NULL DEFAULT '[]'::jsonb;

-- ---------------------------------------------------------------------------
-- 3. Policy class equivalence
-- ---------------------------------------------------------------------------

CREATE TABLE class_equivalence (
    id              serial PRIMARY KEY,
    source_type     varchar(20) NOT NULL CHECK (source_type IN ('sales', 'renewals')),
    source_value    varchar(80) NOT NULL,
    canonical_class varchar(60) NOT NULL,
    note            text,
    updated_by      varchar(120) NOT NULL,
    updated_at      timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_type, source_value)
);

CREATE INDEX ix_class_equivalence_canonical ON class_equivalence (canonical_class);

-- ---------------------------------------------------------------------------
-- 4. Matching
--
-- forecast_actual_match is replaced. Outcome (what happened to the policy) and
-- allocation (which income counts, and to whom) are different questions and
-- are now different tables.
-- ---------------------------------------------------------------------------

DROP VIEW IF EXISTS v_match_summary;
DROP TABLE IF EXISTS forecast_actual_match;

CREATE TABLE policy_outcome (
    id                        bigserial PRIMARY KEY,
    policy_id                 bigint NOT NULL,
    forecast_month            date NOT NULL,
    canonical_manager         varchar(120),
    outcome                   varchar(40) NOT NULL CHECK (outcome IN (
        'renewed', 'transfer_renewed', 'lapsed_lost', 'pending',
        'removed_from_latest', 'multiple_candidates', 'unmatched',
        'manually_resolved')),
    -- The two measures are deliberately separate. Renewal achievement uses the
    -- first; the second is for understanding the whole client relationship.
    renewal_transaction_income numeric(14,2) NOT NULL DEFAULT 0,
    total_associated_income    numeric(14,2) NOT NULL DEFAULT 0,
    original_forecast_income   numeric(14,2) NOT NULL DEFAULT 0,
    latest_forecast_income     numeric(14,2),
    matched_transaction_count  integer NOT NULL DEFAULT 0,
    best_tier                  smallint,
    confidence                 numeric(4,3) CHECK (confidence IS NULL
                                                   OR confidence BETWEEN 0 AND 1),
    requires_review            boolean NOT NULL DEFAULT false,
    is_manual                  boolean NOT NULL DEFAULT false,
    note                       text,
    computed_at                timestamptz NOT NULL DEFAULT now(),
    UNIQUE (policy_id, forecast_month)
);

CREATE INDEX ix_policy_outcome_outcome ON policy_outcome (outcome);
CREATE INDEX ix_policy_outcome_review ON policy_outcome (forecast_month)
    WHERE requires_review;

CREATE TABLE match_allocation (
    id               bigserial PRIMARY KEY,
    transaction_id   bigint NOT NULL REFERENCES sales_transaction(id) ON DELETE CASCADE,
    policy_id        bigint NOT NULL,
    forecast_month   date NOT NULL,
    allocated_income numeric(14,2) NOT NULL,
    -- Whether this allocation counts towards Actual Renewal Income. RWL and TRW
    -- do. An ordinary endorsement attached to the same policy does not: it stays
    -- endorsement income in overall performance.
    is_renewal_income boolean NOT NULL DEFAULT false,
    allocation_basis text NOT NULL,
    method           varchar(10) NOT NULL DEFAULT 'auto' CHECK (method IN ('auto','manual')),
    tier             smallint,
    confidence       numeric(4,3),
    created_by       varchar(120) NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (transaction_id, policy_id, forecast_month)
);

CREATE INDEX ix_match_allocation_policy ON match_allocation (policy_id, forecast_month);
CREATE INDEX ix_match_allocation_txn ON match_allocation (transaction_id);

-- A transaction may be automatically credited to at most one forecast policy.
-- Splitting income across policies is a deliberate manual act, never inferred.
CREATE UNIQUE INDEX uq_auto_allocation_per_transaction
    ON match_allocation (transaction_id) WHERE method = 'auto';

-- Hard guard against double counting: allocations for a transaction may never
-- sum beyond that transaction's income, and may never flip its sign.
CREATE OR REPLACE FUNCTION check_allocation_total() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    txn_income numeric(14,2);
    allocated  numeric(14,2);
BEGIN
    SELECT actual_income INTO txn_income
    FROM sales_transaction WHERE id = NEW.transaction_id;

    SELECT COALESCE(SUM(allocated_income), 0) INTO allocated
    FROM match_allocation WHERE transaction_id = NEW.transaction_id;

    IF txn_income >= 0 AND (allocated < 0 OR allocated > txn_income + 0.001) THEN
        RAISE EXCEPTION
            'allocation total % exceeds income % of transaction %',
            allocated, txn_income, NEW.transaction_id;
    END IF;
    IF txn_income < 0 AND (allocated > 0 OR allocated < txn_income - 0.001) THEN
        RAISE EXCEPTION
            'allocation total % exceeds income % of transaction %',
            allocated, txn_income, NEW.transaction_id;
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER trg_allocation_total
    AFTER INSERT OR UPDATE ON match_allocation
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION check_allocation_total();

-- Candidates that could not be resolved automatically.
CREATE TABLE match_candidate (
    id             bigserial PRIMARY KEY,
    transaction_id bigint REFERENCES sales_transaction(id) ON DELETE CASCADE,
    policy_id      bigint,
    forecast_month date,
    tier           smallint,
    confidence     numeric(4,3),
    reason         varchar(40) NOT NULL CHECK (reason IN (
        'multiple_policies_for_transaction', 'multiple_transactions_for_policy',
        'low_tier_requires_review', 'class_conflict', 'unmatched_actual_renewal',
        'unmatched_forecast_policy')),
    candidate_rank smallint,
    status         varchar(20) NOT NULL DEFAULT 'pending' CHECK (status IN (
        'pending', 'accepted', 'rejected', 'superseded')),
    detail         jsonb,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_match_candidate_pending ON match_candidate (reason)
    WHERE status = 'pending';
CREATE INDEX ix_match_candidate_txn ON match_candidate (transaction_id);

-- Every manual decision, with what it replaced.
CREATE TABLE match_decision (
    id                bigserial PRIMARY KEY,
    policy_id         bigint,
    forecast_month    date,
    transaction_id    bigint,
    action            varchar(20) NOT NULL CHECK (action IN (
        'manual_match', 'reject_match', 'rematch', 'apportion', 'set_outcome')),
    previous_decision jsonb,
    new_decision      jsonb,
    reason            text NOT NULL,
    reviewer          varchar(120) NOT NULL,
    decided_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX ix_match_decision_policy ON match_decision (policy_id, forecast_month);

CREATE TABLE match_run (
    id                    bigserial PRIMARY KEY,
    run_by                varchar(120) NOT NULL,
    run_at                timestamptz NOT NULL DEFAULT now(),
    cut_off_date          date NOT NULL,
    date_tolerance_days   integer NOT NULL,
    forecast_policies     integer NOT NULL DEFAULT 0,
    auto_matched          integer NOT NULL DEFAULT 0,
    auto_matched_income   numeric(14,2) NOT NULL DEFAULT 0,
    review_queue          integer NOT NULL DEFAULT 0,
    unmatched_policies    integer NOT NULL DEFAULT 0,
    unmatched_actuals     integer NOT NULL DEFAULT 0,
    by_tier               jsonb NOT NULL DEFAULT '{}'::jsonb,
    note                  text
);
