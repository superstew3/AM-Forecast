-- Prior-year actual as a forecast origin.
--
-- The work of establishing July 2026 from July 2025 actuals lives in
-- scripts/establish_prior_year_baseline.py, not here. A migration runs before
-- any data is imported, so on a fresh install it would find nothing to work
-- with and July would silently fall back to the legacy dashboard figure. This
-- migration only widens the constraint so the script has an origin to use.

ALTER TABLE original_forecast DROP CONSTRAINT IF EXISTS ck_original_forecast_orig_origin;
ALTER TABLE original_forecast ADD CONSTRAINT ck_original_forecast_orig_origin
  CHECK (origin IN ('snapshot', 'legacy_dashboard', 'prior_year_actual',
                    'manual_entry', 'rebaseline', 'derived_from_actuals'));

COMMENT ON COLUMN original_forecast.origin IS
  'snapshot: from a Renewals Pending file. legacy_dashboard: carried from the '
  'old workbook. prior_year_actual: the same month last year, used where no '
  'policy-level forecast exists. manual_entry: figures supplied directly for a '
  'month with no usable pending forecast. derived_from_actuals: never used, '
  'because a period''s own result must not become its own target.';
