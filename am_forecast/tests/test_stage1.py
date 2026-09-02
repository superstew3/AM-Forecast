"""Stage 1 acceptance tests.

Run against a database that has had the migrations, seed and validation harness
applied. These are the figures that must never drift; if ingest logic changes
and one of these fails, the build fails.

    pytest tests/test_stage1.py --dsn "host=/tmp port=5433 user=postgres dbname=am_forecast"
"""
from __future__ import annotations

from decimal import Decimal

import psycopg2
import pytest

CENT = Decimal("0.01")


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


# --- Section 18: source reconciliation ---------------------------------------

def test_sales_row_counts(conn):
    """Every accepted row is present, and the split adds up.

    Previously pinned to one export's counts, which said nothing about whether
    the import was correct once a different file was loaded.
    """
    total = scalar(conn, "SELECT count(*) FROM sales_transaction")
    excluded = scalar(conn, "SELECT count(*) FROM sales_transaction WHERE is_excluded")
    included = scalar(conn, "SELECT count(*) FROM sales_transaction WHERE NOT is_excluded")
    assert total == excluded + included
    assert total == scalar(conn, """
        SELECT COALESCE(SUM(accepted_row_count), 0) FROM upload_batch
        WHERE status = 'accepted' AND file_type = 'sales'""")

def test_sales_income_totals(conn):
    """Reported income equals what acceptance recorded, and is the SIG share."""
    pos, ret, net = rows(conn, """
        SELECT SUM(positive_income), SUM(signed_return_income), SUM(actual_income)
        FROM sales_transaction WHERE NOT is_excluded""")[0]
    assert abs(pos + ret - net) <= CENT, "positive plus returns must equal net"

    batch_net = scalar(conn, """
        SELECT COALESCE(SUM(net_income), 0) FROM upload_batch
        WHERE status = 'accepted' AND file_type = 'sales'""")
    assert abs(net - batch_net) <= CENT

    # Income is the primary associate share, which is below gross commission
    # and fees. A silent revert to the gross basis would show up here.
    gross = scalar(conn, """SELECT SUM(gross_income) FROM sales_transaction
                            WHERE NOT is_excluded""")
    assert net < gross

def test_renewals_row_counts(conn):
    total = scalar(conn, "SELECT count(*) FROM forecast_policy")
    assert scalar(conn, "SELECT count(DISTINCT policy_id) FROM forecast_policy") == total, \
        "a snapshot must hold each PolicyID once"
    excluded = scalar(conn, "SELECT count(*) FROM forecast_policy WHERE is_excluded")
    included = scalar(conn, "SELECT count(*) FROM forecast_policy WHERE NOT is_excluded")
    assert total == excluded + included

def test_renewals_income_totals(conn):
    """Expected income is the associate share, and contribution floors at zero."""
    raw, contrib = rows(conn, """
        SELECT SUM(raw_expected_income), SUM(forecast_contribution)
        FROM forecast_policy WHERE NOT is_excluded""")[0]
    # Contribution is raw with negatives floored, so it can only be higher.
    assert contrib >= raw

    negatives = scalar(conn, """
        SELECT COALESCE(SUM(raw_expected_income), 0) FROM forecast_policy
        WHERE NOT is_excluded AND raw_expected_income < 0""")
    assert abs(contrib - (raw - negatives)) <= CENT

    gross = scalar(conn, """SELECT SUM(gross_expected_income) FROM forecast_policy
                            WHERE NOT is_excluded""")
    assert raw < gross, "expected income should be the associate share, not gross"

def test_negative_expected_rows(conn):
    """Negative rows contribute zero and stay visible as exceptions."""
    negative = scalar(conn, """SELECT count(*) FROM forecast_policy
                               WHERE NOT is_excluded AND raw_expected_income < 0""")
    assert scalar(conn, """SELECT COALESCE(SUM(forecast_contribution),0)
                           FROM forecast_policy
                           WHERE NOT is_excluded AND raw_expected_income < 0""") == 0
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded
                             AND 'negative_expected' = ANY(exception_flags)""") == negative

def test_zero_expected_policies_are_recognised_as_zero(conn):
    """A policy whose components cancel exactly is a zero.

    Originally pinned to twelve, and to one PolicyID: Comm 206.73 / CommTax
    20.68 offset by Fee -206.73 / FeeTax -20.68. In exact decimal arithmetic
    that is precisely zero; a floating-point pipeline returns 7.1e-15 and counts
    it as non-zero, which is where the brief's eleven came from. The rule is
    that every such policy is both recognised and flagged, whatever the dataset.
    """
    zero_rows = scalar(conn, """SELECT count(*) FROM forecast_policy
                                WHERE NOT is_excluded AND raw_expected_income = 0""")
    flagged = scalar(conn, """SELECT count(*) FROM forecast_policy
                              WHERE NOT is_excluded
                                AND 'zero_expected' = ANY(exception_flags)""")
    assert zero_rows == flagged, "every true zero must carry the exception flag"
    assert scalar(conn, """SELECT COALESCE(SUM(forecast_contribution), 0)
                           FROM forecast_policy
                           WHERE NOT is_excluded AND raw_expected_income = 0""") == 0


@pytest.mark.parametrize("source,canonical", [
    ("Michael Stewart", "Michael Stewart"),
    ("Sam Stewart", "Sam Stewart"),
    ("Cameron Stewart", "Cameron Stewart"),
])
def test_manager_alias_resolution(conn, source, canonical):
    assert scalar(conn, """SELECT canonical_manager FROM v_manager_resolution
                           WHERE source_manager = %s""", (source,)) == canonical


def test_every_source_manager_resolves(conn):
    """No fact row may fall through the alias table unresolved."""
    unresolved = rows(conn, """
        SELECT DISTINCT source_manager FROM v_sales_reported WHERE canonical_manager IS NULL""")
    assert unresolved == []


# --- Section 20 tests 17 and 18: Highview exclusions --------------------------

def test_highview_excluded_by_associate_not_only_by_manager(conn):
    """Test 17: rows excluded on an associate field where the manager is not
    the excluded manager.

    The manager name is read from the exclusion rules rather than written here.
    This test carried the literal 'CAM HIGHVIEW' in its SQL -- the fifth
    hand-written copy of these rules found in this codebase, after the pin
    script, two test helpers and a verification script, each wrong differently
    and none of them visible until a figure disagreed.

    A rule restated in a test is worse than one restated in code: it goes green
    against its own copy while the real rules drift, so the drift is not merely
    unnoticed, it is actively vouched for.
    """
    excluded_managers = [r[0] for r in rows(conn, """
        SELECT DISTINCT upper(match_value) FROM exclusion_rule
        WHERE source_type = 'sales' AND active
          AND target_field IN ('PolicyAccountManager', 'Group1Abbrev')""")]
    if not excluded_managers:
        pytest.skip("no manager-level exclusion rule is configured")

    n = scalar(conn, """
        SELECT count(*) FROM sales_transaction
        WHERE is_excluded
          AND exclusion_field IN ('PrimaryAssocCode', 'SecondaryAssocCode')
          AND NOT (upper(source_manager) = ANY(%s))""", (excluded_managers,))
    assert n > 0, ("no row is excluded on an associate field alone; exclusion "
                   "would then be by manager name only, which loses business "
                   "written under an excluded associate by somebody else")


def test_exclusion_is_by_associate_not_by_manager_name(conn):
    """A manager who also appears under an excluded associate is not lost.

    Cameron Stewart trades under both MMSTEWART and HIGHVIEW. Exclusion applies
    to the associate code, so his non-Highview business must survive. Asserted
    on the rule rather than on a row count belonging to one export.
    """
    excluded_names = {r[0] for r in rows(conn, """
        SELECT DISTINCT source_manager FROM sales_transaction WHERE is_excluded""")}
    retained_names = {r[0] for r in rows(conn, """
        SELECT DISTINCT source_manager FROM sales_transaction WHERE NOT is_excluded""")}
    both = excluded_names & retained_names
    for name in both:
        # Present on both sides: the exclusion cannot have been applied by name.
        assert scalar(conn, """SELECT count(*) FROM sales_transaction
                               WHERE NOT is_excluded AND source_manager = %s""",
                      (name,)) > 0, name

def test_fingerprints_are_unique(conn):
    """Test 20: multiple legitimate lines sharing an invoice number stay separate."""
    assert scalar(conn, "SELECT count(DISTINCT fingerprint) FROM sales_transaction") \
        == scalar(conn, "SELECT count(*) FROM sales_transaction"), \
        "each transaction must have a unique fingerprint"
    assert scalar(conn, """SELECT count(*) FROM (
        SELECT invoice_number FROM sales_transaction
        GROUP BY invoice_number HAVING count(*) > 1) x""") > 0


def test_no_duplicate_transactions_present(conn):
    """Test 19, structural half: the loaded set carries no duplicates.

    The behavioural half — that re-uploading a cumulative file changes no total
    and only increments seen_count — is asserted in
    tests/test_stage2_import.py::test_accepting_a_reupload_changes_no_total,
    which performs the re-upload itself rather than depending on load order.
    """
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") > 0
    assert scalar(conn, "SELECT count(DISTINCT fingerprint) FROM sales_transaction") \
        == scalar(conn, "SELECT count(*) FROM sales_transaction"), \
        "each transaction must have a unique fingerprint"
    net = scalar(conn, """SELECT SUM(actual_income) FROM sales_transaction
                          WHERE NOT is_excluded""")
    assert abs(net - scalar(conn, """
        SELECT COALESCE(SUM(net_income), 0) FROM upload_batch
        WHERE status = 'accepted' AND file_type = 'sales'""")) <= CENT


def test_same_policy_number_different_policy_id_preserved(conn):
    """Distinct PolicyIDs sharing client, policy number and expiry stay distinct.

    Collapsing them would silently merge two policies into one, so the rule is
    that PolicyID remains the identity even where every other field matches.
    """
    total = scalar(conn, "SELECT count(*) FROM forecast_policy WHERE NOT is_excluded")
    distinct_ids = scalar(conn, """SELECT count(DISTINCT policy_id) FROM forecast_policy
                                   WHERE NOT is_excluded""")
    assert total == distinct_ids, "PolicyID must remain the identity"

def test_no_month_has_negative_forecast(conn):
    """Test 5 and 22: a monthly forecast can never go negative."""
    worst = scalar(conn, """
        SELECT MIN(total) FROM (
          SELECT forecast_month, SUM(forecast_contribution) AS total
          FROM forecast_policy WHERE NOT is_excluded GROUP BY 1) x""")
    assert worst >= 0


def test_original_forecast_contribution_never_negative(conn):
    assert scalar(conn, """SELECT count(*) FROM original_forecast
                           WHERE forecast_contribution < 0""") == 0


# --- July 2026 baseline decision ----------------------------------------------

def test_manager_month_baselines_declare_their_origin(conn):
    """A baseline established without policy detail says where it came from.

    Was pinned to July 2026 and to supplied figures totalling $323,349.37. The
    rule is that any manager-month baseline names a real origin, and never the
    period's own actuals.
    """
    for grain, origin, amount in rows(conn, """
            SELECT grain, origin, SUM(forecast_contribution)
            FROM original_forecast GROUP BY 1, 2"""):
        assert origin in ("snapshot", "legacy_dashboard", "prior_year_actual",
                          "manual_entry", "rebaseline"), origin
        if grain == "manager_month":
            assert origin != "snapshot", "a snapshot carries policy detail"

def test_no_original_forecast_is_derived_from_actuals(conn):
    """The result must never become the baseline."""
    assert scalar(conn, """SELECT count(*) FROM original_forecast
                           WHERE origin = 'derived_from_actuals'""") == 0


def test_residual_pending_policies_did_not_establish_a_baseline(conn):
    """Leftovers stay in Latest, flagged, and are never what a month rests on.

    A month whose renewals have mostly already transacted leaves a handful still
    pending in a later extract. Those few must not become the month's baseline:
    measuring a manager against two policies when they wrote four hundred reads
    as a spectacular result and means nothing.

    Originally asserted that July had no Original Forecast at all, which was true
    only while the month had no baseline. July is now pinned from the April
    extract, so the assertion that survives is about provenance — the baseline
    came from a deliberate pin, not from the residual rows — and it is derived
    from the flags rather than naming a month.
    """
    months = rows(conn, """SELECT DISTINCT forecast_month FROM forecast_policy
                           WHERE NOT is_excluded
                             AND 'residual_pending' = ANY(exception_flags)
                           ORDER BY 1""")
    if not months:
        pytest.skip("this export carries no residual pending policies")

    for (month,) in months:
        total = scalar(conn, """SELECT count(*) FROM forecast_policy
                                WHERE NOT is_excluded AND forecast_month = %s""", (month,))
        residual = scalar(conn, """SELECT count(*) FROM forecast_policy
                                   WHERE NOT is_excluded AND forecast_month = %s
                                     AND 'residual_pending' = ANY(exception_flags)""",
                          (month,))
        assert residual == total, \
            f"{month} mixes residual leftovers with live pending policies"
        assert scalar(conn, """SELECT count(*) FROM original_forecast
                               WHERE forecast_month = %s
                                 AND established_snapshot_id IS NOT NULL""", (month,)) == 0, \
            f"{month} was baselined from an import rather than a deliberate pin"


def test_achievement_is_null_not_zero_where_baseline_unusable(conn):
    """N/A, never 0%.

    Reporting 0% against a manager with no usable baseline says they failed,
    when the truth is that they cannot be measured.
    """
    unusable = rows(conn, """
        SELECT period_month, renewal_achievement, baseline_usable
        FROM v_renewal_performance_month WHERE NOT baseline_usable""")
    if not unusable:
        pytest.skip("no unusable baselines in this dataset")
    for month, achievement, usable in unusable:
        assert usable is False, month
        assert achievement is None, month

def test_achievement_is_computed_where_baseline_usable(conn):
    """Where a baseline is usable and income exists, achievement is a figure."""
    usable = rows(conn, """
        SELECT period_month, canonical_manager, renewal_achievement
        FROM v_renewal_performance_month
        WHERE baseline_usable AND renewal_achievement IS NOT NULL LIMIT 5""")
    if not usable:
        pytest.skip("no usable baseline with income in this dataset")
    for month, manager, achievement in usable:
        assert achievement > 0, (month, manager)

def test_months_without_a_baseline_are_declared_unavailable(conn):
    """A month with no baseline says so, and suppresses achievement."""
    unavailable = rows(conn, """
        SELECT forecast_month, baseline_status, suppress_achievement
        FROM forecast_baseline WHERE baseline_status = 'unavailable'""")
    for month, status, suppress in unavailable:
        assert suppress is True, month

def test_budget_is_forecast_plus_growth(conn):
    """Total Budget = Renewal Forecast + growth target, at the applied rate."""
    fy = scalar(conn, """SELECT au_financial_year(cut_off_date)
                         FROM reporting_settings WHERE id = 1""")
    # Row by row, at each row's OWN rate.
    #
    # This summed the whole year and compared it against the GLOBAL rate, which
    # only holds while every manager is on it. The moment one is given an
    # override -- which the budget page exists to allow -- the aggregate stops
    # matching any single rate and the test fails on a legitimate change. A
    # suite that goes red when somebody uses a feature teaches people to ignore
    # it.
    #
    # v_budget_quarter already carries the applied rate per row, and leaves it
    # NULL where a quarter mixes rates. Those rows are skipped rather than
    # guessed at.
    result = rows(conn, """
        SELECT canonical_manager, financial_quarter, growth_pct,
               original_renewal_forecast, new_business_growth_target, total_budget
        FROM v_budget_quarter WHERE financial_year = %s""", (fy,))
    if not result:
        pytest.skip("no budget rows for the current financial year")

    checked = 0
    for who, quarter, rate, orig, target, total in result:
        if orig is None or total is None:
            continue
        # The identity holds for every row regardless of rate.
        assert abs(total - (orig + (target or 0))) <= CENT, (who, quarter)
        if rate is None:
            continue          # the quarter mixes rates; no single one to check
        assert abs((target or 0) - orig * rate) <= Decimal("0.10"), (who, quarter, rate)
        checked += 1

    assert checked, ("no quarter has a single applied rate, so the growth "
                     "relationship could not be checked anywhere")

def test_non_ranked_managers_count_to_totals_but_not_rankings(conn):
    """Rankings and business totals answer different questions.

    Named after Anastasia K originally, which tied it to one roster. A manager
    excluded from rankings must still count towards the business, and a zero
    denominator must yield NULL rather than 0%.
    """
    non_ranked = [r[0] for r in rows(conn, """
        SELECT canonical_manager FROM reporting_manager
        WHERE NOT include_in_rankings""")]
    assert non_ranked, "the fixture should include at least one non-ranked manager"

    for name in non_ranked:
        assert scalar(conn, """SELECT include_in_business_totals
                               FROM reporting_manager
                               WHERE canonical_manager = %s""", (name,)) is True, name
        # Where no budget applies, achievement must be NULL, never zero.
        budget = scalar(conn, """SELECT COALESCE(SUM(total_budget), 0)
                                 FROM v_budget_quarter
                                 WHERE canonical_manager = %s""", (name,))
        if budget == 0:
            assert scalar(conn, """SELECT bool_or(renewal_achievement IS NOT NULL)
                                   FROM v_renewal_income_month
                                   WHERE canonical_manager = %s""",
                          (name,)) in (False, None), name

def test_growth_rate_override_affects_only_that_manager(conn):
    """Test 24."""
    before = {m: v for m, v in rows(conn, """
        SELECT canonical_manager, SUM(total_budget) FROM v_budget_quarter
        WHERE financial_year = 2026 GROUP BY 1""")}
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO growth_rate
            (scope, canonical_manager, financial_year, financial_quarter, growth_pct,
             note, active, created_by)
            VALUES ('manager_quarter','Sam Stewart',2026,1,0.2000,'test',true,'pytest')""")
    after = {m: v for m, v in rows(conn, """
        SELECT canonical_manager, SUM(total_budget) FROM v_budget_quarter
        WHERE financial_year = 2026 GROUP BY 1""")}
    conn.rollback()
    assert after["Sam Stewart"] > before["Sam Stewart"]
    for m in before:
        if m != "Sam Stewart":
            assert after[m] == before[m]


# --- Partial period labelling -------------------------------------------------

def test_fy2024_25_labelled_partial(conn):
    status, months = rows(conn, """
        SELECT coverage_status, months_present FROM period_coverage
        WHERE financial_year = 2024 AND data_domain = 'actuals'""")[0]
    assert status == "partial"
    assert months == 2


def test_all_categories_mapped(conn):
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE business_classification = 'Unmapped'""") == 0
