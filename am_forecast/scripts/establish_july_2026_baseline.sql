-- Establish the July 2026 baseline from figures supplied directly, then lock it.
--
--   psql "$DATABASE_URL" -f scripts/establish_july_2026_baseline.sql
--
-- July cannot be derived from any file we hold. A pending-renewals report lists
-- only what has not yet transacted, so an extract taken during or after July has
-- already lost everything that renewed in it -- the 14 July file carries 211
-- July policies worth $182,416.57, well short of the real month. Reconstructing
-- from it would set a target that is wrong in the direction that flatters
-- everyone, which is the worst direction for a target to be wrong in.
--
-- These figures were supplied directly instead. They are forecast income on the
-- associate basis; the growth uplift is applied on read, exactly as it is for an
-- imported month, so the raw forecast stays visible and reconcilable.
--
-- Recorded at manager_month grain because that is the grain they arrived at.
-- Inventing policy rows to fill a policy-grain baseline would manufacture detail
-- nobody supplied and make the month look sourced when it was stated.

BEGIN;

CREATE TEMP TABLE july_supplied (manager text, forecast_income numeric(14,2));
INSERT INTO july_supplied VALUES
    ('AnneM Goodchild',   16756.02),
    ('Cameron Stewart',     104.90),
    ('Houseboats SIG',     4442.68),
    ('Liam Thornton',     43806.15),
    ('Maddie Commins',    32254.75),
    ('Marine Trades',      2451.21),
    ('Michael Stewart',   70177.69),
    ('Rebekah Shone',     20838.64),
    ('Retail',            40059.70),
    ('Sam Stewart',       37278.45),
    ('Shannen Giles',     33780.02),
    ('Strata Insurance',   9054.80),
    ('Thomasina Troumb',  20671.02);

-- Every supplied name must resolve to a known reporting manager. Four arrived
-- shortened -- Liam, Michael, Sam, Shannen -- and are expanded above rather than
-- matched loosely at run time: a fuzzy match that silently picked the wrong
-- manager would move a target from one person to another.
SELECT s.manager AS unrecognised_manager
FROM july_supplied s
LEFT JOIN reporting_manager rm ON rm.canonical_manager = s.manager
WHERE rm.canonical_manager IS NULL;

DO $$
DECLARE bad int;
BEGIN
    SELECT count(*) INTO bad FROM july_supplied s
    LEFT JOIN reporting_manager rm ON rm.canonical_manager = s.manager
    WHERE rm.canonical_manager IS NULL;
    IF bad > 0 THEN
        RAISE EXCEPTION 'One or more supplied managers do not exist. Nothing written.';
    END IF;
END $$;

-- Anything already sitting on July is replaced wholesale. A partial overwrite
-- would leave the month split across two sources and two bases.
DELETE FROM original_forecast WHERE forecast_month = DATE '2026-07-01';

INSERT INTO original_forecast
    (grain, policy_id, forecast_month, financial_year, financial_quarter,
     origin, established_by, source_manager, expected_income,
     forecast_contribution, income_basis, basis_verified_by, basis_verified_at, note)
SELECT 'manager_month', NULL, DATE '2026-07-01',
       au_financial_year(DATE '2026-07-01'), au_quarter(DATE '2026-07-01'),
       'manual_entry', 'sam:supplied', s.manager, s.forecast_income,
       s.forecast_income, 'associate', 'sam:supplied', now(),
       'July 2026 forecast supplied directly. No renewals extract covers July '
       'completely: an extract taken during or after the month has already lost '
       'whatever renewed in it.'
FROM july_supplied s;

-- Lock it. July is a past month and would be frozen by the calendar rule anyway;
-- the explicit lock records why it is not to be touched even by an override that
-- someone might grant for a different reason later.
INSERT INTO forecast_month_lock
    (forecast_month, locked_by, reason, source_description, forecast_total, active)
VALUES (DATE '2026-07-01', 'sam:supplied',
        'Figures supplied directly; no extract covers July completely',
        'Manual entry, 13 managers, associate basis',
        (SELECT SUM(forecast_income) FROM july_supplied), true)
ON CONFLICT (forecast_month) DO UPDATE
SET locked_by = EXCLUDED.locked_by, reason = EXCLUDED.reason,
    source_description = EXCLUDED.source_description,
    forecast_total = EXCLUDED.forecast_total, active = true,
    released_at = NULL, released_by = NULL, release_reason = NULL;

INSERT INTO budget_audit
    (action, scope_description, financial_year, financial_quarter,
     before_value, after_value, reason, performed_by)
VALUES ('establish_month_baseline', 'July 2026, all managers',
        au_financial_year(DATE '2026-07-01'), au_quarter(DATE '2026-07-01'),
        NULL,
        (SELECT jsonb_build_object('july_2026_forecast', SUM(forecast_income),
                                   'july_2026_target', round(SUM(forecast_income) * 1.075, 2),
                                   'managers', count(*))
         FROM july_supplied),
        'Supplied directly: no renewals extract covers July completely',
        'sam:supplied');

-- What was written.
SELECT source_manager AS manager,
       to_char(forecast_contribution, 'FM999,999,990.00')       AS forecast_income,
       to_char(forecast_contribution * 1.075, 'FM999,999,990.00') AS target_income,
       income_basis
FROM original_forecast
WHERE forecast_month = DATE '2026-07-01'
ORDER BY forecast_contribution DESC;

SELECT count(*) AS managers,
       to_char(SUM(forecast_contribution), 'FM999,999,990.00')       AS july_forecast,
       to_char(SUM(forecast_contribution) * 1.075, 'FM999,999,990.00') AS july_target
FROM original_forecast WHERE forecast_month = DATE '2026-07-01';

COMMIT;
