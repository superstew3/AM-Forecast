-- 0024: remove three views nothing reads.
--
--     v_prior_year_comparison
--     v_manager_transfer_detail
--     v_budget_performance_quarter
--
-- Each verified unreferenced before dropping: no endpoint, no script, no test,
-- no page, and no other view. They were built for reports that took a different
-- shape by the time the pages were written.
--
-- Worth removing rather than leaving. A view that looks authoritative and is
-- maintained by nobody is a trap: the next person needing prior-year figures
-- finds v_prior_year_comparison, uses it, and inherits whatever assumptions were
-- true when it was written and have not been checked since. This codebase has
-- already shown what happens when two things that look equivalent disagree --
-- a view and a function one character apart gave opposite answers about whether
-- a month could be written, and nothing caught it.
--
-- The definitions remain in the migration history if any is ever wanted back.

DROP VIEW IF EXISTS v_prior_year_comparison CASCADE;
DROP VIEW IF EXISTS v_manager_transfer_detail CASCADE;
DROP VIEW IF EXISTS v_budget_performance_quarter CASCADE;
