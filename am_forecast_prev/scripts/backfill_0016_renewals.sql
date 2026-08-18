-- Backfill the renewals associate columns after applying migration 0016.
--
-- RUN THIS IMMEDIATELY AFTER 0016, BEFORE TRUSTING ANY FIGURE.
--
-- Why it is needed
-- ----------------
-- 0016 handles sales and renewals differently, and only one of them survives an
-- in-place migration.
--
--   Sales     primary_assoc_amount already exists in the 0015 schema and the
--             importer already wrote it. The generated column picks it up and
--             income moves correctly to the associate basis.
--
--   Renewals  primary_assoc_comm_sum and primary_assoc_comm_tax_sum DO NOT
--             exist before 0016. The migration adds them as
--             NOT NULL DEFAULT 0, then makes forecast_contribution a generated
--             column reading them.
--
-- So on a database whose renewals were imported before 0016, every existing
-- policy gets zero, and forecast_contribution becomes 0.00 for the entire book.
-- The renewal forecast, the budget derived from it, and every achievement and
-- bonus figure beneath it all go to zero.
--
-- The migration reports success. Nothing errors. Verified: a book of
-- $200,188.42 became $0.00 with a clean migration run.
--
-- Why a backfill works
-- --------------------
-- forecast_policy.source_row retains the complete original CSV row as jsonb --
-- the "nothing is silently dropped at import" rule paying for itself. The two
-- columns the migration needs are still there and can be read straight back.
--
-- Rows whose source_row lacks the keys are left at zero and counted at the end.
-- Anything other than zero there means those policies must be re-imported.

BEGIN;

UPDATE forecast_policy p
SET primary_assoc_comm_sum =
        COALESCE(NULLIF(regexp_replace(p.source_row->>'PrimaryAssocCommSum',
                                       '[^0-9.\-]', '', 'g'), ''), '0')::numeric,
    primary_assoc_comm_tax_sum =
        COALESCE(NULLIF(regexp_replace(p.source_row->>'PrimaryAssocCommTaxSum',
                                       '[^0-9.\-]', '', 'g'), ''), '0')::numeric,
    primary_assoc_abbrev = p.source_row->>'PrimaryAssocAbbrev'
WHERE p.source_row ? 'PrimaryAssocCommSum';

-- ---------------------------------------------------------------------------
-- The budget baseline has to be rebased too. This is the dangerous half.
-- ---------------------------------------------------------------------------
-- original_forecast.forecast_contribution is a STORED column, not generated, and
-- 0016 does not touch that table. So the two halves of the renewal book behave
-- differently under an in-place migration:
--
--   forecast_policy    generated over the new associate columns -> reads 0.00.
--                      Obvious, alarming, and therefore safe.
--
--   original_forecast  stored, untouched -> survives intact ON THE OLD GROSS
--                      BASIS. Nothing looks wrong. Every budget target stays
--                      about 6% too high, permanently, while actuals move to
--                      the associate basis underneath them. Variance and
--                      achievement are then wrong for every manager, in the
--                      flattering direction, with no symptom at all.
--
-- The rebase joins each baseline row back to its policy, whose source_row still
-- holds the associate figures. Rows that cannot be joined -- a month pinned from
-- a separate file, for instance -- are counted below and left alone rather than
-- guessed at.

UPDATE original_forecast o
SET expected_income = fp.raw_expected_income,
    forecast_contribution = fp.forecast_contribution
FROM forecast_policy fp
WHERE fp.policy_id = o.policy_id
  AND fp.forecast_month = o.forecast_month
  AND fp.source_row ? 'PrimaryAssocCommSum';

-- Baseline rows that could not be rebased. Report these: they are still on the
-- gross basis and their managers' targets are overstated until they are fixed.
SELECT o.forecast_month,
       count(*) AS baseline_rows_still_on_gross_basis,
       to_char(SUM(o.forecast_contribution), 'FM999,999,990.00') AS affected_total
FROM original_forecast o
LEFT JOIN forecast_policy fp
       ON fp.policy_id = o.policy_id AND fp.forecast_month = o.forecast_month
WHERE fp.policy_id IS NULL
GROUP BY o.forecast_month ORDER BY o.forecast_month;

-- What could not be recovered. Must be zero.
SELECT count(*) AS policies_without_source_associate_data
FROM forecast_policy
WHERE NOT (source_row ? 'PrimaryAssocCommSum');

-- The recovered book. Compare against what the source file reports.
SELECT to_char(SUM(forecast_contribution), 'FM999,999,990.00') AS forecast_contribution,
       to_char(SUM(gross_expected_income), 'FM999,999,990.00') AS gross_for_audit,
       count(*)                                                AS policies
FROM forecast_policy WHERE NOT is_excluded;

-- Inspect both figures before committing. The contribution should sit a few per
-- cent below the gross, and the gross should still reconcile to the source
-- report exactly as it did before the migration.
--
-- If either looks wrong, ROLLBACK instead.
COMMIT;
