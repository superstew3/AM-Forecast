-- 0025: the operating position, in one place, in English.
--
-- Everything this view reports was already knowable. actual_load_state() knew
-- whether a month was fully imported, v_original_forecast_month knew which
-- months had a forecast, upload_batch knew when a file last arrived. None of it
-- was gathered anywhere a person would look, so knowing whether the system
-- needed attention meant knowing which four screens to check and what a healthy
-- answer looked like on each.
--
-- The wording lives here rather than in the interface, for the same reason
-- v_month_performance.status_note does: a rule restated in a second place drifts
-- from the first, and the drift is invisible because both sides look confident.
-- The page renders these strings; it decides nothing.
--
-- Three severities, deliberately few:
--
--   ok         nothing to do
--   attention  something is due, or will be soon; no figure is wrong yet
--   action     a figure on a page is unavailable or unscoreable until this is
--              dealt with
--
-- The distinction that matters is between "due" and "wrong". A sales file that
-- has not arrived on the 2nd of the month is not a fault -- the export does not
-- exist yet, because WinBEAT publishes by accounting period. The same file
-- missing on the 20th means the month that closed three weeks ago still cannot
-- be scored. Collapsing those into one warning teaches the reader to ignore
-- both.
--
-- Two things are deliberately NOT the basis of a check here:
--
--   v_missing_forecast_month  is driven by v_actual_month, so it can only see a
--                             month that already carries transactions. A month
--                             that begins with no target AND no actuals -- the
--                             exact case worth catching early -- is invisible to
--                             it. It remains correct for what the performance
--                             page asks of it; it is the wrong instrument here.
--
--   v_monthly_budget          is absent for a month whose forecast is perfectly
--                             fine but whose growth rate will not resolve:
--                             resolve_growth_month() returns no rows when
--                             growth_rate holds nothing active at any scope, and
--                             the CROSS JOIN LATERAL then drops the month.
--                             Testing "is there a forecast" against it reports a
--                             confident red on a month that has one. The two
--                             conditions are separated below because they have
--                             completely different fixes: one needs an audited
--                             override and an upload, the other needs a rate set
--                             on the Budget page.

CREATE OR REPLACE VIEW v_operational_status AS
WITH clock AS (
    -- Every date here comes from the calendar in Melbourne, through the same
    -- function the rest of the system uses. Nothing reads the stored cut-off: it
    -- has decided nothing since 0020, and a panel whose job is to say what needs
    -- attention must not be the last place still consulting it.
    SELECT reporting_current_month()                                AS current_month,
           (now() AT TIME ZONE 'Australia/Melbourne')::date         AS today,
           (reporting_current_month() - INTERVAL '1 month')::date   AS last_completed_month,
           (reporting_current_month() + INTERVAL '1 month')::date   AS next_month,
           (reporting_current_month() + INTERVAL '1 month'
                                      - INTERVAL '1 day')::date     AS month_end,
           (reporting_current_month() + INTERVAL '2 month'
                                      - INTERVAL '1 day')::date     AS next_month_end,
           make_date(au_financial_year(reporting_current_month()), 7, 1)
                                                                    AS financial_year_start
),
months AS (
    -- The current financial year, plus the month about to start.
    --
    -- Scoped, not exhaustive. Listing every month the database has ever held put
    -- fourteen FY2025-26 months on an FY2026-27 page the last time this was done
    -- unscoped, which buried the two that were real. The month ahead is included
    -- because it is the one still cheaply fixable: once it begins, only an
    -- audited override can give it a target.
    SELECT gs::date AS month
    FROM clock c,
         generate_series(c.financial_year_start, c.next_month, INTERVAL '1 month') gs
),
renewals AS (
    SELECT max(b.uploaded_at AT TIME ZONE 'Australia/Melbourne')::date AS last_upload
    FROM upload_batch b
    WHERE b.file_type = 'renewals' AND b.status = 'accepted'
),
renewal_state AS (
    SELECT r.last_upload,
           CASE WHEN r.last_upload IS NULL THEN NULL
                ELSE ((EXTRACT(YEAR  FROM c.current_month) - EXTRACT(YEAR  FROM r.last_upload)) * 12
                    + (EXTRACT(MONTH FROM c.current_month) - EXTRACT(MONTH FROM r.last_upload)))::int
           END                                                      AS months_since,
           -- The routine says pull it in the last days of the month. Seven, so
           -- the reminder arrives with a working week left in it rather than on
           -- the afternoon it is already too late to be careful about.
           (c.today >= c.month_end - 6)                             AS in_due_window
    FROM clock c CROSS JOIN renewals r
),
sales AS (
    -- The span of accepted sales imports, read from the same coverage the
    -- load-state functions use rather than re-derived from upload_batch. One
    -- implementation, so "loaded to" on this panel cannot disagree with
    -- "actuals not loaded" on the performance page.
    SELECT (upper(actual_coverage()) - 1)                           AS coverage_to,
           date_trunc('month', lower(actual_coverage()))::date      AS first_covered_month
),
missing AS (
    -- No forecast at all for the month.
    SELECT m.month,
           month_state(m.month)                                     AS state,
           EXISTS (SELECT 1 FROM forecast_month_override o
                   WHERE o.forecast_month = m.month
                     AND o.consumed_at IS NULL)                     AS override_pending
    FROM months m
    WHERE NOT EXISTS (SELECT 1 FROM v_original_forecast_month f
                      WHERE f.forecast_month = m.month)
),
unbudgeted AS (
    -- A forecast that produces no target. Separate condition, separate fix.
    SELECT m.month
    FROM months m
    WHERE EXISTS (SELECT 1 FROM v_original_forecast_month f
                  WHERE f.forecast_month = m.month)
      AND NOT EXISTS (SELECT 1 FROM v_monthly_budget b
                      WHERE b.forecast_month = m.month)
),
holes AS (
    -- Completed months behind the frontier that are not fully imported.
    --
    -- The last completed month is deliberately excluded: it has its own check,
    -- because a gap there is the routine running late and a gap behind it is a
    -- file that never landed. Bounded below by the first month any import
    -- covers, so months predating the system are not reported as missing.
    SELECT m.month,
           actual_load_state(m.month)                               AS load_state,
           actual_loaded_to(m.month)                                AS loaded_to
    FROM months m, clock c, sales s
    WHERE m.month < c.last_completed_month
      AND m.month >= s.first_covered_month
      AND actual_load_state(m.month) <> 'full'
)

-- 1. The renewals extract -----------------------------------------------------
--
-- What is recorded is when the file was UPLOADED, not when it was pulled out of
-- WinBEAT. Nothing in the export states its own extract date, and
-- forecast_snapshot.as_of_date is inferred from the earliest pending month, so
-- it carries month resolution at best. The upload date is the honest answer and
-- is described as one rather than dressed up as a pull date.
SELECT 1                                                            AS sort_order,
       'renewals_extract'                                           AS check_key,
       'Renewals extract'                                           AS title,
       CASE WHEN r.last_upload IS NULL      THEN 'action'
            WHEN r.months_since >= 2        THEN 'action'
            WHEN r.months_since = 0         THEN 'ok'
            WHEN r.in_due_window            THEN 'attention'
            ELSE 'ok' END                                           AS severity,
       CASE WHEN r.last_upload IS NULL
                THEN 'No renewals extract has ever been uploaded'
            WHEN r.months_since = 0
                THEN 'Uploaded ' || to_char(r.last_upload, 'DD Mon YYYY')
                     || ' - this month is done'
            WHEN r.months_since = 1 AND r.in_due_window
                THEN 'Due now - last uploaded ' || to_char(r.last_upload, 'DD Mon YYYY')
            WHEN r.months_since = 1
                THEN 'Uploaded ' || to_char(r.last_upload, 'DD Mon YYYY')
            ELSE r.months_since || ' months since the last upload ('
                 || to_char(r.last_upload, 'DD Mon YYYY') || ')'
       END                                                          AS headline,
       CASE WHEN r.last_upload IS NULL
                THEN 'Every month ahead is without a target until one is loaded.'
            WHEN r.months_since >= 2
                THEN 'A month has been skipped. Every month set since '
                     || to_char(r.last_upload, 'Mon YYYY')
                     || ' took its target from that one file, so policies written '
                     || 'or lost in between are in none of them.'
            ELSE 'The date shown is when the file was uploaded. The extract''s own '
                 || 'pull date is not recorded anywhere - pull and upload on the '
                 || 'same day and the two are the same thing.'
       END                                                          AS detail,
       CASE WHEN r.last_upload IS NULL OR r.months_since >= 2 OR r.in_due_window
                THEN 'Pull the Renewals Pending Summary from WinBEAT and upload it '
                     || 'on Uploads & audit. It sets '
                     || to_char(c.next_month, 'Mon YYYY')
                     || ' onward; it cannot change this month or any month before it.'
            ELSE 'Nothing to do. Pull the next one in the last days of the month.'
       END                                                          AS what_to_do,
       r.last_upload                                                AS as_at,
       CASE WHEN r.months_since = 0 THEN c.next_month_end
            ELSE c.month_end END                                    AS next_due,
       0                                                            AS item_count
FROM clock c CROSS JOIN renewal_state r

UNION ALL

-- 2. The sales actuals --------------------------------------------------------
SELECT 2,
       'sales_actuals',
       'Sales actuals',
       CASE WHEN actual_load_state(c.last_completed_month) = 'full' THEN 'ok'
            -- The export for a month that has just closed does not exist on the
            -- 1st: WinBEAT publishes by accounting period, and periods are
            -- sometimes mid-month. Due, not late.
            WHEN c.today < c.current_month + 7                      THEN 'attention'
            ELSE 'action' END,
       CASE WHEN s.coverage_to IS NULL
                THEN 'No sales transactions have been imported'
            WHEN actual_load_state(c.last_completed_month) = 'full'
                THEN 'Loaded to ' || to_char(s.coverage_to, 'DD Mon YYYY')
                     || ' - ' || to_char(c.last_completed_month, 'Mon YYYY')
                     || ' is complete'
            WHEN actual_load_state(c.last_completed_month) = 'partial'
                THEN to_char(c.last_completed_month, 'Mon YYYY')
                     || ' is only part loaded'
                     || COALESCE(' (to ' || to_char(
                            actual_loaded_to(c.last_completed_month), 'DD Mon') || ')', '')
            ELSE to_char(c.last_completed_month, 'Mon YYYY')
                 || ' has no transactions imported'
       END,
       CASE WHEN actual_load_state(c.last_completed_month) = 'full'
                THEN 'The month under way shows income to '
                     || COALESCE(to_char(actual_loaded_to(c.current_month), 'DD Mon'),
                                 'no imported day yet')
                     || '. A part month is expected there and is never scored.'
            ELSE to_char(c.last_completed_month, 'Mon YYYY')
                 || ' has closed but cannot be scored until every day of it is '
                 || 'imported. Achievement and bonus read N/A for it, and the '
                 || 'outlook falls back to expected income rather than treating '
                 || 'the month as nil.'
       END,
       CASE WHEN actual_load_state(c.last_completed_month) = 'full'
                THEN 'Nothing to do. Pull the next Sales Transaction List in the '
                     || 'first days of ' || to_char(c.next_month, 'Mon YYYY') || '.'
            ELSE 'Pull the Sales Transaction List for '
                 || to_char(c.last_completed_month, 'Mon YYYY')
                 || ' from WinBEAT and upload it. It is add-only and dedupes on a '
                 || 'row fingerprint, so re-uploading a file that overlaps what is '
                 || 'already loaded is safe and adds only what is new.'
       END,
       s.coverage_to,
       CASE WHEN actual_load_state(c.last_completed_month) = 'full'
            THEN c.next_month ELSE NULL::date END,
       0
FROM clock c CROSS JOIN sales s

UNION ALL

-- 3. The month about to start -------------------------------------------------
--
-- The one warning that arrives while it is still cheap to act on. After the 1st
-- this month falls into check 4, and the fix stops being an upload and starts
-- being an audited override.
SELECT 3,
       'next_month_forecast',
       'Next month''s target',
       CASE WHEN count(x.month) = 0 THEN 'ok'
            WHEN bool_or(r.in_due_window) THEN 'action'
            ELSE 'attention' END,
       CASE WHEN count(x.month) = 0
                THEN to_char(max(c.next_month), 'Mon YYYY') || ' has a forecast'
            ELSE to_char(max(c.next_month), 'Mon YYYY') || ' has no forecast yet'
       END,
       CASE WHEN count(x.month) = 0
                THEN 'Set from the last renewals extract. A newer extract will still '
                     || 'replace it right up until the month begins.'
            ELSE 'It freezes on ' || to_char(max(c.next_month), 'DD Mon')
                 || '. After that a routine upload cannot write it, and it needs an '
                 || 'administrator override instead.'
       END,
       CASE WHEN count(x.month) = 0 THEN 'Nothing to do.'
            ELSE 'Upload a Renewals Pending Summary before '
                 || to_char(max(c.next_month), 'DD Mon') || '.'
       END,
       NULL::date,
       max(c.month_end),
       count(x.month)::int
FROM clock c
CROSS JOIN renewal_state r
LEFT JOIN missing x ON x.month = c.next_month

UNION ALL

-- 4. Months that began without a forecast -------------------------------------
SELECT 4,
       'missing_forecast',
       'Months with no forecast',
       CASE WHEN count(*) = 0 THEN 'ok' ELSE 'action' END,
       CASE WHEN count(*) = 0
                THEN 'Every month that has started has a forecast'
            ELSE count(*) || ' month' || CASE WHEN count(*) = 1 THEN '' ELSE 's' END
                 || ' started with no forecast: '
                 || string_agg(to_char(x.month, 'Mon YYYY'), ', ' ORDER BY x.month)
       END,
       CASE WHEN count(*) = 0
                THEN 'Nothing is being measured against a target that was never set.'
            ELSE 'A routine upload cannot fill these. Achievement, budget and bonus '
                 || 'read Missing Forecast for every manager in them.'
                 || CASE WHEN count(*) FILTER (WHERE x.override_pending) > 0
                         THEN ' An override is already granted and unused for '
                              || string_agg(to_char(x.month, 'Mon YYYY'), ', ')
                                 FILTER (WHERE x.override_pending)
                              || ' - the next upload covering it will write it.'
                         ELSE '' END
       END,
       CASE WHEN count(*) = 0 THEN 'Nothing to do.'
            ELSE 'Settings -> Forecast months -> Reopen the month, with a reason, '
                 || 'then upload a renewals extract that covers it. The override is '
                 || 'single use and is recorded against the figure it produces.'
       END,
       NULL::date,
       NULL::date,
       count(*)::int
FROM missing x WHERE x.state <> 'future'

UNION ALL

-- 5. Holes behind the frontier ------------------------------------------------
SELECT 5,
       'partial_months',
       'Part loaded months',
       CASE WHEN count(*) = 0 THEN 'ok' ELSE 'action' END,
       CASE WHEN count(*) = 0
                THEN 'Every completed month before the last one is fully loaded'
            ELSE count(*) || ' completed month'
                 || CASE WHEN count(*) = 1 THEN ' is' ELSE 's are' END
                 || ' not fully loaded: '
                 || string_agg(to_char(h.month, 'Mon YYYY')
                        || COALESCE(' (to ' || to_char(h.loaded_to, 'DD Mon') || ')',
                                    ' (nothing imported)'),
                        ', ' ORDER BY h.month)
       END,
       CASE WHEN count(*) = 0
                THEN 'No gaps between the imports that have been accepted.'
            ELSE 'These months are closed and cannot be scored. This is a gap in the '
                 || 'middle of the imports rather than the routine running late, so '
                 || 'a later file will not close it by itself.'
       END,
       CASE WHEN count(*) = 0 THEN 'Nothing to do.'
            ELSE 'Pull the Sales Transaction List covering the whole of each month '
                 || 'listed and upload it. It is add-only, so overlapping what is '
                 || 'already there costs nothing.'
       END,
       NULL::date,
       NULL::date,
       count(*)::int
FROM holes h

UNION ALL

-- 6. Forecasts that produce no target -----------------------------------------
--
-- A month with a forecast and no budget row. The cause is the growth rate:
-- resolve_growth_month() returns nothing when growth_rate holds no active row at
-- any scope, and v_monthly_budget's CROSS JOIN LATERAL then drops the month
-- entirely. Every figure derived from budget -- target, achievement, bonus,
-- outlook gap -- is absent for it, and nothing on any other page says why.
SELECT 6,
       'budget_resolution',
       'Growth rate',
       CASE WHEN count(*) = 0 THEN 'ok' ELSE 'action' END,
       CASE WHEN count(*) = 0
                THEN 'Every forecast month resolves to a budget'
            ELSE count(*) || ' month' || CASE WHEN count(*) = 1 THEN '' ELSE 's' END
                 || ' with a forecast but no target: '
                 || string_agg(to_char(u.month, 'Mon YYYY'), ', ' ORDER BY u.month)
       END,
       CASE WHEN count(*) = 0
                THEN 'The growth hierarchy resolves for every manager and month in '
                     || 'the year.'
            ELSE 'The forecast for these months is loaded and correct. What is '
                 || 'missing is a growth rate to apply to it, so no budget, '
                 || 'achievement or bonus figure exists for them.'
       END,
       CASE WHEN count(*) = 0 THEN 'Nothing to do.'
            ELSE 'Budget -> set the default growth rate. It applies to every '
                 || 'manager without one of their own.'
       END,
       NULL::date,
       NULL::date,
       count(*)::int
FROM unbudgeted u;

COMMENT ON VIEW v_operational_status IS
    'One row per operational check, carrying the wording the panel renders. '
    'severity is ok | attention | action: attention means something is due, '
    'action means a figure on a page is unavailable until it is dealt with. '
    'Migration state is NOT here -- it compares files on disk against the '
    'database and only the running process can see both.';
