-- 0018: two independent ledgers, and a freeze boundary that moves by itself.
--
-- Replaces the single reporting cut-off as the thing that decides whether a
-- month is actual or forecast. It never should have been: a cut-off set one
-- month forward made a part month's income read as a full month's result, and
-- every manager in it looked catastrophically behind for reasons that had
-- nothing to do with their performance.
--
-- The model now:
--
--   Actual ledger    Transactions, always. A month shows what has been earned
--                    in it so far. An incomplete month is not a problem, it is
--                    a month-to-date figure and is labelled as one. Forecast
--                    never replaces it.
--
--   Expected ledger  Forecast files, always. A month freezes the moment it
--                    begins, so a target cannot be rewritten after people have
--                    started being measured against it. Only months after the
--                    current one are overwritten by a new upload.
--
-- Neither upload touches the other ledger. Every month carries both figures,
-- plus variance and achievement.
--
-- The boundary is derived from today's date rather than stored, so nobody has
-- to maintain it and it cannot be left at a stale value. cut_off_date survives
-- for the things that are genuinely about data coverage -- match tolerance,
-- coverage warnings -- and no longer decides actual versus forecast.

-- ---------------------------------------------------------------------------
-- The current month, in the timezone the business actually operates in.
-- ---------------------------------------------------------------------------
-- Melbourne, explicitly. The server clock is UTC and runs ten hours behind, so
-- at 9am on the first of a month it still reads the last day of the previous
-- one. For those ten hours a month that has already started would still count
-- as future, and a forecast uploaded in that window would overwrite a month
-- people were already being measured against -- silently, and only on one
-- morning a month, which is exactly the kind of fault that survives testing.
CREATE OR REPLACE FUNCTION reporting_current_month() RETURNS date
LANGUAGE sql STABLE AS $$
    SELECT date_trunc('month', (now() AT TIME ZONE 'Australia/Melbourne'))::date;
$$;

COMMENT ON FUNCTION reporting_current_month() IS
    'First day of the current calendar month in Australia/Melbourne. The single '
    'source of the actual/expected boundary. Never use CURRENT_DATE for this.';


-- ---------------------------------------------------------------------------
-- Where a month sits relative to today.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION month_state(m date) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN m < reporting_current_month()  THEN 'completed'
        WHEN m = reporting_current_month()  THEN 'in_progress'
        ELSE 'future'
    END;
$$;

COMMENT ON FUNCTION month_state(date) IS
    'completed | in_progress | future. in_progress is never scored: a full '
    'month target against a part month actual is not a result.';


-- ---------------------------------------------------------------------------
-- Whether a forecast month may be rewritten by an upload.
-- ---------------------------------------------------------------------------
-- A month at or before the current one is closed to normal uploads, whether or
-- not it already carries a figure. A month that began without a target does not
-- get one filled in retrospectively by a routine upload -- it is reported as
-- Missing Forecast and requires a deliberate, audited decision instead. Filling
-- it quietly would let a target appear weeks after the period it measures.
CREATE OR REPLACE FUNCTION forecast_month_is_open(m date) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT m > reporting_current_month()
       AND NOT EXISTS (SELECT 1 FROM forecast_month_lock l
                       WHERE l.forecast_month = m AND l.active);
$$;

COMMENT ON FUNCTION forecast_month_is_open(date) IS
    'True when a routine forecast upload may overwrite this month. False for '
    'the current month, every past month, and any month explicitly pinned.';


-- ---------------------------------------------------------------------------
-- Admin override: establish or replace a frozen month, with an audit trail.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forecast_month_override (
    id                serial PRIMARY KEY,
    forecast_month    date        NOT NULL,
    granted_by        varchar(120) NOT NULL,
    granted_at        timestamptz NOT NULL DEFAULT now(),
    reason            text        NOT NULL,
    consumed_at       timestamptz,
    consumed_batch_id integer REFERENCES upload_batch (id),
    before_total      numeric(14,2),
    after_total       numeric(14,2)
);

CREATE INDEX IF NOT EXISTS ix_forecast_month_override_open
    ON forecast_month_override (forecast_month) WHERE consumed_at IS NULL;

COMMENT ON TABLE forecast_month_override IS
    'A single deliberate permission to write a frozen forecast month. Granted '
    'by an administrator with a reason, consumed by one upload, and retained '
    'afterwards so the before and after figures stay answerable.';


CREATE OR REPLACE FUNCTION forecast_month_writable(m date) RETURNS boolean
LANGUAGE sql STABLE AS $$
    SELECT forecast_month_is_open(m)
        OR EXISTS (SELECT 1 FROM forecast_month_override o
                   WHERE o.forecast_month = m AND o.consumed_at IS NULL);
$$;


-- ---------------------------------------------------------------------------
-- Which income basis each baseline row is actually on.
-- ---------------------------------------------------------------------------
-- Rows established before the associate-income change hold gross figures.
-- original_forecast.forecast_contribution is a stored column, so migrating does
-- not convert them and nothing about them looks wrong -- the target is simply
-- about 6% too high while actuals sit on the associate basis beneath it.
--
-- Flagging alone is not enough. A row on an unverified basis is excluded from
-- achievement and from bonus until a reconstructed baseline is approved, because
-- a target nobody has confirmed is not something to measure a person against.
ALTER TABLE original_forecast
    ADD COLUMN IF NOT EXISTS income_basis varchar(24) NOT NULL DEFAULT 'associate';

ALTER TABLE original_forecast
    ADD COLUMN IF NOT EXISTS basis_verified_by varchar(120);

ALTER TABLE original_forecast
    ADD COLUMN IF NOT EXISTS basis_verified_at timestamptz;

COMMENT ON COLUMN original_forecast.income_basis IS
    'associate = confirmed on the primary associate basis and scoreable. '
    'gross_unverified = predates the change, could not be rebased, excluded '
    'from achievement and bonus until an audited reconstruction is approved.';


CREATE OR REPLACE VIEW v_baseline_basis_month AS
SELECT o.forecast_month                                              AS month,
       count(*)                                                      AS baseline_rows,
       count(*) FILTER (WHERE o.income_basis = 'associate')          AS rows_associate,
       count(*) FILTER (WHERE o.income_basis <> 'associate')         AS rows_unverified,
       SUM(o.forecast_contribution)
           FILTER (WHERE o.income_basis <> 'associate')              AS value_unverified,
       bool_and(o.income_basis = 'associate')                        AS scoreable
FROM original_forecast o
GROUP BY o.forecast_month;

COMMENT ON VIEW v_baseline_basis_month IS
    'Whether a month rests entirely on confirmed associate-basis figures. A '
    'month with any unverified row is not scored at all -- part of a target on '
    'one basis and part on another is not a target.';


-- ---------------------------------------------------------------------------
-- Forecast, target and lock state per manager-month.
-- ---------------------------------------------------------------------------
-- Three separate figures, never collapsed into one. The raw forecast is what
-- the file said; the target is that figure with the growth uplift applied. The
-- uplift is applied on read, so the imported forecast is never overwritten by
-- the number derived from it and can always be shown, checked and reconciled
-- against the source file.
CREATE OR REPLACE VIEW v_expected_month AS
SELECT b.canonical_manager,
       b.forecast_month,
       b.financial_year,
       b.financial_quarter,
       b.original_forecast                      AS forecast_income,
       b.total_budget                           AS target_income,
       CASE WHEN b.original_forecast > 0
            THEN round(b.total_budget / b.original_forecast, 4)
            ELSE NULL END                       AS uplift_applied,
       month_state(b.forecast_month)            AS month_state,
       forecast_month_is_open(b.forecast_month) AS accepts_upload,
       COALESCE(bb.scoreable, true)             AS basis_scoreable
FROM v_monthly_budget b
LEFT JOIN v_baseline_basis_month bb ON bb.month = b.forecast_month;

COMMENT ON VIEW v_expected_month IS
    'The expected-income ledger. forecast_income is the imported figure on the '
    'associate basis; target_income is that times the growth uplift. Driven only '
    'by forecast uploads -- a transaction import never changes a row here.';


-- ---------------------------------------------------------------------------
-- Which months the transaction ledger actually covers, and how completely.
-- ---------------------------------------------------------------------------
-- A completed month with a target and no transactions is ambiguous, and the two
-- readings are miles apart: either nobody earned anything, or nobody has
-- uploaded the file yet. Reported as zero it puts every manager at 0% and reads
-- as a disaster.
--
-- "Covered" is deliberately strict. The first attempt treated a month as loaded
-- if it fell anywhere between a batch's first and last date, which was wrong
-- three ways: a file running to the 11th marked the whole month loaded; a file
-- spanning a month boundary marked both months loaded when neither was; and two
-- files with a gap between them marked the gap loaded. Each would have scored a
-- manager on a fraction of their month.
--
-- Coverage is therefore the union of covered DAYS across accepted sales batches,
-- and a completed month counts as loaded only when every day of it is in that
-- union. Partial coverage is its own state -- fine for the month in progress,
-- where month-to-date is the whole point, and not scoreable for a month that
-- has closed.
CREATE OR REPLACE FUNCTION actual_coverage() RETURNS datemultirange
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(range_agg(daterange(b.coverage_start, b.coverage_end, '[]')),
                    '{}'::datemultirange)
    FROM upload_batch b
    WHERE b.file_type = 'sales'
      AND b.status = 'accepted'
      AND b.coverage_start IS NOT NULL
      AND b.coverage_end IS NOT NULL;
$$;

COMMENT ON FUNCTION actual_coverage() IS
    'Union of days covered by accepted sales imports. Built from the coverage '
    'each file records, not from whether rows landed -- a quiet week is not an '
    'unloaded one.';


CREATE OR REPLACE FUNCTION actual_load_state(m date) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT CASE
        WHEN actual_coverage() @> daterange(m, (m + INTERVAL '1 month')::date, '[)')
            THEN 'full'
        WHEN actual_coverage() && daterange(m, (m + INTERVAL '1 month')::date, '[)')
            THEN 'partial'
        ELSE 'none'
    END;
$$;

COMMENT ON FUNCTION actual_load_state(date) IS
    'full | partial | none. A completed month is only scoreable on full.';


CREATE OR REPLACE FUNCTION actual_loaded_to(m date) RETURNS date
LANGUAGE sql STABLE AS $$
    -- The end of the CONTINUOUS run from the first of the month, not the last
    -- covered day anywhere in it. Two files covering the 1st to the 10th and the
    -- 20th to the 30th would otherwise report "loaded to the 30th" over a hole in
    -- the middle, which is a worse claim than admitting the month is incomplete:
    -- it invites the reader to treat the figure as month-to-date when nine days
    -- are missing from the middle of it.
    --
    -- Null when the month has no coverage starting at its first day. There is no
    -- honest "to" date for a month whose beginning is missing.
    SELECT (upper(r) - 1)
    FROM unnest(actual_coverage()
                * datemultirange(daterange(m, (m + INTERVAL '1 month')::date, '[)'))) AS r
    WHERE lower(r) = m
    LIMIT 1;
$$;

COMMENT ON FUNCTION actual_loaded_to(date) IS
    'Last day of an unbroken run of imported transactions from the first of the '
    'month. Null if the month does not start covered. What the UI shows beside a '
    'month-to-date figure so the reader knows how far it runs.';


-- ---------------------------------------------------------------------------
-- Actual and expected side by side. Every month carries both.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS v_outlook_month_v2 CASCADE;
DROP VIEW IF EXISTS v_performance_quarter CASCADE;
DROP VIEW IF EXISTS v_month_performance CASCADE;
CREATE VIEW v_month_performance AS
WITH combined AS (
    SELECT COALESCE(e.canonical_manager, a.canonical_manager)  AS canonical_manager,
           COALESCE(e.forecast_month, a.period_month)          AS month,
           COALESCE(e.financial_year, a.financial_year)        AS financial_year,
           COALESCE(e.financial_quarter, a.financial_quarter)  AS financial_quarter,
           e.forecast_income,
           e.target_income,
           e.uplift_applied,
           COALESCE(e.basis_scoreable, true)                   AS basis_scoreable,
           a.net_actual_income                                 AS actual_income,
           a.transaction_rows
    FROM v_expected_month e
    FULL JOIN v_actual_month a
           ON a.canonical_manager = e.canonical_manager
          AND a.period_month      = e.forecast_month
), flagged AS (
    SELECT c.*,
           month_state(c.month)        AS ms,
           actual_load_state(c.month)  AS load_state,
           actual_loaded_to(c.month)   AS loaded_to,
           -- Scoreable only when the whole month is in. The month in progress is
           -- never scored anyway, so partial coverage there is expected.
           (actual_load_state(c.month) = 'full') AS actuals_loaded
    FROM combined c
)
SELECT f.canonical_manager,
       f.month,
       f.financial_year,
       f.financial_quarter,
       f.ms                                                    AS month_state,
       f.forecast_income,
       f.target_income,
       f.uplift_applied,
       -- Month-to-date for the current month, final for a completed one. Null
       -- where the month has not started, and null where no transaction file
       -- covers it: a future month has not earned zero, and an unimported month
       -- is unknown rather than nil.
       -- Month-to-date while a month runs, final once it closes. Null where the
       -- month has not started, and null where nothing covers it: a future month
       -- has not earned zero, and an unimported one is unknown rather than nil.
       CASE WHEN f.ms = 'future' OR f.load_state = 'none' THEN NULL
            ELSE COALESCE(f.actual_income, 0) END              AS actual_income,
       f.loaded_to                                             AS actual_income_to,
       CASE WHEN f.ms = 'future' OR f.load_state = 'none' THEN NULL
            ELSE COALESCE(f.actual_income, 0)
                 - COALESCE(f.target_income, 0) END            AS variance,
       -- Achievement is withheld for a month that has not started, for one
       -- still running, and for one whose transactions are not in yet. A part
       -- month's income against a whole month's target is not a percentage
       -- anybody should act on, and neither is an empty month's.
       -- Withheld for a month not started, one still running, one whose
       -- transactions are not in, and one resting on a baseline whose income
       -- basis has not been confirmed.
       CASE WHEN f.ms <> 'completed' OR NOT f.actuals_loaded THEN NULL
            WHEN NOT f.basis_scoreable THEN NULL
            WHEN COALESCE(f.target_income, 0) = 0 THEN NULL
            ELSE round(100.0 * COALESCE(f.actual_income, 0)
                       / f.target_income, 1) END               AS achievement_pct,
       CASE
           WHEN f.target_income IS NULL AND f.ms <> 'future'    THEN 'missing_forecast'
           WHEN f.ms = 'future'                                 THEN 'not_started'
           WHEN NOT f.basis_scoreable                           THEN 'baseline_unverified'
           WHEN f.ms = 'in_progress'                            THEN 'in_progress'
           WHEN f.load_state = 'none'                           THEN 'actuals_not_loaded'
           WHEN f.load_state = 'partial'                        THEN 'actuals_partial'
           WHEN COALESCE(f.actual_income, 0)
                >= COALESCE(f.target_income, 0)               THEN 'achieved'
           ELSE 'below_target'
       END                                                     AS status,
       -- Exact wording for the UI, defined here rather than in the frontend, so
       -- a fallback can never be shown as if it were a measured figure.
       CASE
           WHEN f.target_income IS NULL AND f.ms <> 'future'
               THEN 'Missing forecast - no target was set before this month began'
           WHEN f.ms = 'future'                THEN NULL
           WHEN NOT f.basis_scoreable
               THEN 'Baseline not on the confirmed associate basis - excluded '
                    || 'from achievement and bonus until a reconstructed '
                    || 'baseline is approved'
           WHEN f.ms = 'in_progress' AND f.load_state = 'none'
               THEN 'Month in progress - no actuals loaded yet'
           WHEN f.ms = 'in_progress' AND f.loaded_to IS NOT NULL
               THEN 'Month in progress - actual income to ' || to_char(f.loaded_to, 'DD Mon')
           WHEN f.ms = 'in_progress'
               THEN 'Month in progress - transactions loaded do not cover the '
                    || 'start of the month'
           WHEN f.load_state = 'none'
               THEN 'Actuals not loaded - outlook using expected income'
           WHEN f.load_state = 'partial' AND f.loaded_to IS NOT NULL
               THEN 'Actuals loaded only to ' || to_char(f.loaded_to, 'DD Mon')
                    || ' - outlook using expected income'
           WHEN f.load_state = 'partial'
               THEN 'Actuals only partly loaded and the month does not start '
                    || 'covered - outlook using expected income'
           ELSE NULL
       END                                                     AS status_note,
       f.load_state                                            AS actuals_load_state,
       f.actuals_loaded,
       f.basis_scoreable,
       COALESCE(f.transaction_rows, 0)                         AS transaction_rows
FROM flagged f;

COMMENT ON VIEW v_month_performance IS
    'Both ledgers per manager-month. status is in_progress for the current '
    'month and never achieved or below_target -- a month still running has no '
    'result. missing_forecast marks a month that began with no target.';


-- ---------------------------------------------------------------------------
-- Months that began without a target.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE VIEW v_missing_forecast_month AS
SELECT DISTINCT a.period_month                    AS month,
       month_state(a.period_month)                AS month_state,
       EXISTS (SELECT 1 FROM forecast_month_override o
               WHERE o.forecast_month = a.period_month
                 AND o.consumed_at IS NULL)       AS override_pending
FROM v_actual_month a
WHERE a.period_month <= reporting_current_month()
  AND NOT EXISTS (SELECT 1 FROM v_monthly_budget b
                  WHERE b.forecast_month = a.period_month);

COMMENT ON VIEW v_missing_forecast_month IS
    'A month carrying actuals but no expected income, which a routine upload is '
    'not allowed to fill once the month has started. Needs an audited override.';


-- ---------------------------------------------------------------------------
-- Latest Outlook, rebuilt.
-- ---------------------------------------------------------------------------
-- Actual for months that have finished, expected for the current month and for
-- every month ahead. The current month takes expected deliberately: it is a
-- projection of where the year lands, and month-to-date would drag it down for
-- three weeks and recover at month end, which is movement that means nothing.
CREATE VIEW v_outlook_month_v2 AS
SELECT p.canonical_manager,
       p.month,
       p.financial_year,
       p.financial_quarter,
       p.month_state,
       -- A completed month whose transactions are not loaded falls back to
       -- expected. Treating it as zero would quietly delete a month of income
       -- from the projection because a file had not arrived yet.
       CASE WHEN p.month_state = 'completed' AND p.actuals_loaded
            THEN COALESCE(p.actual_income, 0)
            ELSE COALESCE(p.target_income, 0) END   AS outlook_income,
       CASE WHEN p.month_state = 'completed' AND p.actuals_loaded
            THEN 'actual'
            WHEN p.month_state = 'completed'
            THEN 'expected_fallback'
            ELSE 'expected' END                     AS outlook_basis,
       CASE WHEN p.month_state = 'completed' AND NOT p.actuals_loaded
            THEN 'Actuals not loaded - outlook using expected income'
            ELSE NULL END                           AS outlook_note
FROM v_month_performance p;


CREATE VIEW v_performance_quarter AS
SELECT p.canonical_manager,
       p.financial_year,
       p.financial_quarter,
       SUM(p.forecast_income)                                   AS forecast_income,
       SUM(p.target_income)                                     AS target_income,
       SUM(p.actual_income)                                     AS actual_income,
       SUM(p.actual_income) FILTER (WHERE p.month_state = 'completed'
                                      AND p.basis_scoreable)    AS actual_income_scoreable,
       SUM(p.target_income) FILTER (WHERE p.month_state = 'completed'
                                      AND p.basis_scoreable)    AS target_income_scoreable,
       count(*) FILTER (WHERE NOT p.basis_scoreable)            AS months_basis_unverified,
       (SELECT SUM(o.outlook_income) FROM v_outlook_month_v2 o
        WHERE o.canonical_manager  = p.canonical_manager
          AND o.financial_year     = p.financial_year
          AND o.financial_quarter  = p.financial_quarter)        AS latest_outlook,
       count(*) FILTER (WHERE p.month_state = 'in_progress')     AS months_in_progress,
       count(*) FILTER (WHERE p.status = 'missing_forecast')     AS months_missing_forecast,
       -- Scored on completed months alone. Including a month still running
       -- would put a whole quarter's target against a part quarter's income.
       CASE WHEN SUM(p.target_income) FILTER (WHERE p.month_state = 'completed'
                                                AND p.basis_scoreable) > 0
            THEN round(100.0 * SUM(p.actual_income) FILTER (WHERE p.month_state = 'completed'
                                                              AND p.basis_scoreable)
                       / SUM(p.target_income) FILTER (WHERE p.month_state = 'completed'
                                                        AND p.basis_scoreable), 1)
            ELSE NULL END                                        AS achievement_pct_completed
FROM v_month_performance p
GROUP BY p.canonical_manager, p.financial_year, p.financial_quarter;

COMMENT ON VIEW v_performance_quarter IS
    'achievement_pct_completed deliberately excludes a month still running, and '
    'is null until at least one month in the quarter has closed. months_in_progress '
    'tells the caller why.';
