-- 0021: new business by month, with counts.
--
-- v_new_business_analysis only ever grouped by quarter, and reported money
-- without saying how many transactions produced it. Two consequences:
--
--   The page could not offer a month view, because the grain did not exist.
--
--   A manager with one large new policy and a manager with fifteen small ones
--   read identically. For new business in particular that is the more useful
--   half of the picture: the count is the activity, the money is the outcome.
--
-- The growth target and its achievement are dropped from this view. New business
-- is measured here; the growth target is a budget concept and lives on the
-- budget page. Carrying it alongside invited the comparison the page kept making
-- -- a quarter's target against a part quarter's new business, which reported
-- everyone at a fraction of a goal for reasons of arithmetic.

DROP VIEW IF EXISTS v_new_business_analysis CASCADE;

CREATE VIEW v_new_business_analysis AS
SELECT COALESCE(r.canonical_manager, t.source_manager) AS canonical_manager,
       t.financial_year,
       t.financial_quarter,
       t.period_month,

       -- Money.
       SUM(t.actual_income) FILTER (
           WHERE t.category = 'N/B' AND t.actual_income > 0)      AS gross_new_business,
       SUM(t.absolute_return_income) FILTER (
           WHERE t.category = 'N/B' AND t.actual_income < 0)      AS negative_new_business_corrections,
       SUM(t.absolute_return_income) FILTER (
           WHERE t.category = 'NCN')                              AS new_business_cancellations,
       SUM(t.actual_income) FILTER (
           WHERE t.category IN ('N/B', 'NCN'))                    AS net_new_business,

       -- Counts. Written separately rather than as one total, because a
       -- cancellation and a correction are different events and lumping them
       -- together loses the distinction the money columns preserve.
       count(*) FILTER (
           WHERE t.category = 'N/B' AND t.actual_income > 0)      AS new_business_count,
       count(*) FILTER (
           WHERE t.category = 'N/B' AND t.actual_income < 0)      AS correction_count,
       count(*) FILTER (
           WHERE t.category = 'NCN')                              AS cancellation_count,
       count(*) FILTER (
           WHERE t.category IN ('N/B', 'NCN'))                    AS transaction_count
FROM sales_transaction t
LEFT JOIN v_manager_resolution r ON r.source_manager = t.source_manager
WHERE NOT t.is_excluded
GROUP BY COALESCE(r.canonical_manager, t.source_manager),
         t.financial_year, t.financial_quarter, t.period_month;

COMMENT ON VIEW v_new_business_analysis IS
    'New business by manager and month, with counts as well as money. A quarter '
    'view is this summed; the month grain is the base so both are the same '
    'figures rather than two calculations that can drift apart.';
