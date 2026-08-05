"""Stage 4 acceptance tests: matching, income attribution, review, coverage.

Every test that changes state cleans up after itself, so the database is left at
the accepted base position: the supplied snapshot, cut-off 31 July 2026, and no
synthetic transactions.
"""
from __future__ import annotations

import datetime as dt
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from app.importers import accept, prepare, rollback
from app.importers.service import ImportError_
from app.matching import apportion, manual_match, reject_match, run_matching

from conftest import RENEWALS_FILE

CENT = Decimal("0.01")
ROOT = Path(__file__).resolve().parents[1]
BASE_CUT_OFF = dt.date(2026, 7, 31)
FIXTURE_MONTH = dt.date(2026, 8, 1)


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def set_cut_off(conn, value: dt.date) -> None:
    with conn.cursor() as cur:
        cur.execute("UPDATE reporting_settings SET cut_off_date=%s WHERE id=1", (value,))
    conn.commit()


@pytest.fixture(scope="module")
def matched(request, tmp_path_factory):
    """Import the synthetic fixture, advance the cut-off, run matching.

    Torn down completely: the batch is rolled back, the cut-off restored and the
    matcher re-run, so the production position is exactly as it was.
    """
    import psycopg2
    dsn = request.config.getoption("--dsn")
    conn = psycopg2.connect(dsn)
    path = tmp_path_factory.mktemp("fixture") / "match_fixture.csv"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_match_fixture.py"),
                    dsn, str(path), f"--month={FIXTURE_MONTH}"], check=True,
                   capture_output=True)
    s = prepare(conn, str(path), "pytest")
    accept(conn, s.batch_id, "pytest")
    set_cut_off(conn, dt.date(2026, 8, 31))
    result = run_matching(conn, "pytest")
    yield conn, result, s.batch_id
    rollback(conn, s.batch_id, "test teardown", "pytest")
    set_cut_off(conn, BASE_CUT_OFF)
    run_matching(conn, "pytest")
    conn.close()


# --- tier ordering ------------------------------------------------------------

def test_class_agreement_earns_the_top_tier(matched):
    """Class agreement must raise a match, never lower it.

    A clean renewal with an agreeing class sits at tier 1; the same match with a
    conflicting class falls to tier 2, never below a policy-number match.
    """
    conn, _, _ = matched
    tiers = dict(rows(conn, """SELECT tier, count(*) FROM match_allocation
                               WHERE method='auto' GROUP BY 1"""))
    assert tiers.get(1, 0) > 0
    assert tiers.get(2, 0) > 0
    top = scalar(conn, """SELECT MIN(confidence) FROM match_allocation WHERE tier=1""")
    second = scalar(conn, """SELECT MAX(confidence) FROM match_allocation WHERE tier=2""")
    assert top > second


def test_class_conflict_demotes_to_tier_two_not_lower(matched):
    """A policy-number match with a conflicting class is still strong evidence."""
    conn, _, _ = matched
    conflicted = rows(conn, """
        SELECT a.tier FROM match_allocation a
        JOIN sales_transaction t ON t.id = a.transaction_id
        WHERE t.policy_class = 'MARINEHULL'""")
    assert conflicted
    assert all(t == 2 for (t,) in conflicted)


def test_tier_four_always_requires_review(matched):
    """Client + class without a policy number is never credited automatically."""
    conn, _, _ = matched
    assert scalar(conn, "SELECT count(*) FROM match_allocation WHERE tier=4") == 0
    assert scalar(conn, """SELECT count(*) FROM match_candidate
                           WHERE reason='low_tier_requires_review'""") > 0


def test_tiers_are_ordered_by_confidence(matched):
    conn, _, _ = matched
    seen = rows(conn, """SELECT tier, MAX(confidence) FROM match_allocation
                         WHERE method='auto' GROUP BY 1 ORDER BY 1""")
    confidences = [c for _, c in seen]
    assert confidences == sorted(confidences, reverse=True)


# --- outcome versus income ----------------------------------------------------

def test_ordinary_endorsement_is_not_renewal_income(matched):
    """An endorsement on a renewed policy stays endorsement income."""
    conn, _, _ = matched
    flagged = rows(conn, """
        SELECT a.is_renewal_income FROM match_allocation a
        JOIN sales_transaction t ON t.id = a.transaction_id
        WHERE t.category = 'END'
          AND t.invoice_number NOT IN (
            SELECT t2.invoice_number FROM match_allocation a2
            JOIN sales_transaction t2 ON t2.id = a2.transaction_id
            WHERE t2.category IN ('RWL','TRW') AND t2.invoice_number IS NOT NULL)""")
    assert flagged
    assert all(f is False for (f,) in flagged)


def test_adjustment_sharing_a_renewal_invoice_is_renewal_income(matched):
    """An invoice chain is the defensible link that lets a correction count."""
    conn, _, _ = matched
    chained = rows(conn, """
        SELECT a.is_renewal_income, a.allocation_basis FROM match_allocation a
        JOIN sales_transaction t ON t.id = a.transaction_id
        WHERE t.category = 'ADJ' AND t.invoice_number >= 8800000""")
    assert chained
    assert all(f is True and "invoice chain" in b for f, b in chained)


def test_lapse_produces_lost_outcome_with_zero_renewal_income(matched):
    """A lapse is a lost renewal, not negative renewal income achieved."""
    conn, _, _ = matched
    outcomes = rows(conn, """
        SELECT renewal_transaction_income, total_associated_income
        FROM policy_outcome WHERE outcome = 'lapsed_lost'""")
    assert outcomes
    for renewal, total in outcomes:
        assert renewal == 0
        assert total < 0  # the negative lapse income is still recorded


def test_lapse_income_still_reduces_net_actual_and_shows_as_return(matched):
    """The lapse must not vanish from overall performance."""
    conn, _, _ = matched
    lapse_return = scalar(conn, """
        SELECT SUM(absolute_return_income) FROM sales_transaction
        WHERE NOT is_excluded AND category='LAP' AND invoice_number >= 8800000""")
    assert lapse_return > 0
    in_analysis = scalar(conn, """
        SELECT SUM(absolute_return_income) FROM v_return_income_analysis
        WHERE derived_classification = 'Lapse / Lost Renewal'""")
    assert in_analysis >= lapse_return


def test_two_income_measures_are_distinct(matched):
    """Renewal income and total associated income answer different questions."""
    conn, _, _ = matched
    differing = scalar(conn, """
        SELECT count(*) FROM policy_outcome
        WHERE renewal_transaction_income <> total_associated_income""")
    assert differing > 0


def test_outcomes_cover_the_required_vocabulary(matched):
    conn, result, _ = matched
    assert {"renewed", "transfer_renewed", "lapsed_lost", "pending"} <= set(result.by_outcome)


def test_open_renewal_window_is_pending_not_unmatched(matched):
    """A policy expiring days before the cut-off has not failed to renew."""
    conn, _, _ = matched
    assert scalar(conn, """
        SELECT count(*) FROM policy_outcome po
        JOIN forecast_policy fp ON fp.policy_id = po.policy_id
        WHERE po.outcome = 'unmatched'
          AND fp.expiry_date > DATE '2026-08-31' - 45""") == 0


# --- duplicate allocation -----------------------------------------------------

def test_one_transaction_is_never_auto_credited_to_two_policies(matched):
    """The core double-counting guard."""
    conn, _, _ = matched
    assert scalar(conn, """
        SELECT COALESCE(MAX(c), 0) FROM (
            SELECT count(*) AS c FROM match_allocation
            WHERE method='auto' GROUP BY transaction_id) x""") <= 1


def test_allocation_never_exceeds_the_source_transaction(matched):
    conn, _, _ = matched
    assert scalar(conn, "SELECT count(*) FROM v_allocation_breaches") == 0


def test_class_resolves_contention_where_it_can(matched):
    """Twins sharing client and policy number but differing in class: the named
    class wins and the other gets nothing."""
    conn, _, _ = matched
    credited = rows(conn, """
        SELECT a.policy_id, fp.class_abbrev, t.policy_class
        FROM match_allocation a
        JOIN sales_transaction t ON t.id = a.transaction_id
        JOIN LATERAL (SELECT class_abbrev FROM forecast_policy p
                      WHERE p.policy_id = a.policy_id LIMIT 1) fp ON true
        WHERE t.client_code IN ('CONSTRUCT1','PROWORLD') AND t.invoice_number >= 8800000""")
    assert credited
    for _, policy_class, txn_class in credited:
        assert policy_class.upper().startswith("LIAB")


def test_unresolvable_contention_goes_to_review_uncredited(matched):
    """Where class cannot separate the twins, nothing is credited."""
    conn, _, _ = matched
    contended = rows(conn, """
        SELECT transaction_id, count(*) FROM match_candidate
        WHERE reason='multiple_policies_for_transaction' GROUP BY 1""")
    assert contended
    for txn, competing in contended:
        assert competing > 1
        assert scalar(conn, """SELECT count(*) FROM match_allocation
                               WHERE transaction_id=%s AND method='auto'""", (txn,)) == 0


def test_trigger_rejects_a_deliberate_double_credit(matched):
    """The guard is enforced by the database, not only by the matcher.

    The constraint trigger is DEFERRABLE INITIALLY DEFERRED, so it fires at
    COMMIT rather than at statement time. The test has to commit to observe it.
    """
    conn, _, _ = matched
    import psycopg2
    try:
        with pytest.raises(psycopg2.errors.RaiseException):
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO match_allocation (transaction_id, policy_id, forecast_month,
                        allocated_income, is_renewal_income, allocation_basis, method,
                        created_by)
                    SELECT a.transaction_id, 999999999, a.forecast_month, a.allocated_income,
                           true, 'deliberate double credit', 'manual', 'pytest'
                    FROM match_allocation a WHERE a.method='auto' LIMIT 1""")
            conn.commit()
    finally:
        conn.rollback()
    assert scalar(conn, """SELECT count(*) FROM match_allocation
                           WHERE policy_id = 999999999""") == 0


# --- manual review ------------------------------------------------------------

def test_manual_match_records_reviewer_reason_and_previous_decision(matched):
    conn, _, _ = matched
    txn, pids, month = rows(conn, """
        SELECT transaction_id, array_agg(policy_id), min(forecast_month)
        FROM match_candidate WHERE reason='multiple_policies_for_transaction'
          AND status='pending' GROUP BY 1 LIMIT 1""")[0]
    manual_match(conn, pids[0], month, txn, "reviewer.a", "checked underwriter schedule")
    try:
        reviewer, action, reason, prev, new = rows(conn, """
            SELECT reviewer, action, reason, previous_decision, new_decision
            FROM match_decision WHERE transaction_id=%s ORDER BY id DESC LIMIT 1""",
                                                   (txn,))[0]
        assert reviewer == "reviewer.a"
        assert action == "manual_match"
        assert reason == "checked underwriter schedule"
        assert new is not None
        assert scalar(conn, """SELECT method FROM match_allocation
                               WHERE transaction_id=%s""", (txn,)) == "manual"
        assert scalar(conn, """SELECT status FROM match_candidate
                               WHERE transaction_id=%s AND policy_id=%s""",
                      (txn, pids[1])) == "rejected"
    finally:
        reject_match(conn, txn, "pytest", "test cleanup")


def test_apportionment_splits_without_exceeding_the_transaction(matched):
    conn, _, _ = matched
    txn, pids, month = rows(conn, """
        SELECT transaction_id, array_agg(policy_id), min(forecast_month)
        FROM match_candidate WHERE reason='multiple_policies_for_transaction'
        GROUP BY 1 LIMIT 1""")[0]
    income = scalar(conn, "SELECT actual_income FROM sales_transaction WHERE id=%s", (txn,))
    apportion(conn, txn,
              [(pids[0], month, (income * Decimal("0.6")).quantize(CENT)),
               (pids[1], month, (income * Decimal("0.4")).quantize(CENT))],
              "reviewer.b", "two sections invoiced together")
    try:
        allocated = scalar(conn, """SELECT SUM(allocated_income) FROM match_allocation
                                    WHERE transaction_id=%s""", (txn,))
        assert allocated == income
        assert scalar(conn, """SELECT status FROM v_allocation_integrity
                               WHERE transaction_id=%s""", (txn,)) == "ok"
        assert scalar(conn, """SELECT count(*) FROM match_allocation
                               WHERE transaction_id=%s""", (txn,)) == 2
    finally:
        reject_match(conn, txn, "pytest", "test cleanup")


def test_apportionment_beyond_the_transaction_is_rejected(matched):
    conn, _, _ = matched
    import psycopg2
    txn, pids, month = rows(conn, """
        SELECT transaction_id, array_agg(policy_id), min(forecast_month)
        FROM match_candidate WHERE reason='multiple_policies_for_transaction'
        GROUP BY 1 LIMIT 1""")[0]
    income = scalar(conn, "SELECT actual_income FROM sales_transaction WHERE id=%s", (txn,))
    with pytest.raises(psycopg2.errors.RaiseException):
        apportion(conn, txn, [(pids[0], month, income), (pids[1], month, income)],
                  "reviewer.c", "deliberately over-allocated")
    conn.rollback()


def test_rerunning_the_matcher_preserves_manual_decisions(matched):
    conn, _, _ = matched
    txn, pids, month = rows(conn, """
        SELECT transaction_id, array_agg(policy_id), min(forecast_month)
        FROM match_candidate WHERE reason='multiple_policies_for_transaction'
        GROUP BY 1 LIMIT 1""")[0]
    manual_match(conn, pids[0], month, txn, "reviewer.d", "manual decision to preserve")
    try:
        run_matching(conn, "pytest")
        assert scalar(conn, """SELECT method FROM match_allocation
                               WHERE transaction_id=%s""", (txn,)) == "manual"
        assert scalar(conn, """SELECT count(*) FROM match_decision
                               WHERE transaction_id=%s""", (txn,)) >= 1
    finally:
        reject_match(conn, txn, "pytest", "test cleanup")
        run_matching(conn, "pytest")


# --- coverage confirmation ----------------------------------------------------

def test_absent_month_is_not_treated_as_mass_removal(conn, tmp_path):
    """A narrower export must not wipe an otherwise valid Latest Forecast."""
    df = pl.read_csv(RENEWALS_FILE, infer_schema_length=0)
    narrow = df.filter(pl.col("ExpiryDate") < "2026-12-01")
    p = tmp_path / "narrow.csv"
    narrow.write_csv(p)

    before = scalar(conn, "SELECT SUM(latest_forecast) FROM v_latest_forecast_month")
    s = prepare(conn, str(p), "pytest")
    try:
        assert s.requires_confirmation
        assert any("absent from this file" in m for m in s.messages)
        # Accept is blocked without an explicit coverage declaration.
        from app.importers import AcceptError
        with pytest.raises(AcceptError) as exc:
            accept(conn, s.batch_id, "pytest")
        assert "coverage confirmation" in str(exc.value)
    finally:
        from app.importers import reject
        reject(conn, s.batch_id, "test cleanup", "pytest")
    assert scalar(conn, "SELECT SUM(latest_forecast) FROM v_latest_forecast_month") == before


def test_confirmed_months_limit_what_can_be_removed(conn, tmp_path):
    """Only months the uploader confirms are compared for removals."""
    df = pl.read_csv(RENEWALS_FILE, infer_schema_length=0)
    narrow = df.filter(pl.col("ExpiryDate") < "2026-12-01")
    p = tmp_path / "narrow2.csv"
    narrow.write_csv(p)

    before_dec = scalar(conn, """SELECT SUM(latest_forecast) FROM v_latest_forecast_month
                                 WHERE forecast_month = DATE '2026-12-01'""")
    s = prepare(conn, str(p), "pytest")
    accept(conn, s.batch_id, "pytest",
           confirmed_months=[dt.date(2026, 9, 1), dt.date(2026, 10, 1)])
    try:
        # December was never confirmed, so it is untouched.
        after_dec = scalar(conn, """SELECT SUM(latest_forecast) FROM v_latest_forecast_month
                                    WHERE forecast_month = DATE '2026-12-01'""")
        assert after_dec == before_dec
        assert scalar(conn, """SELECT count(*) FROM forecast_movement
                               WHERE forecast_month = DATE '2026-12-01'""") == 0
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)


# --- multi-attribute movement -------------------------------------------------

def test_movement_flags_are_independent(conn, tmp_path):
    """A policy that changes manager AND amount must count as both."""
    df = pl.read_csv(RENEWALS_FILE, infer_schema_length=0)
    fut = df.filter(pl.col("ExpiryDate") >= "2026-09-01")
    both = fut.slice(0, 30).with_columns(
        pl.lit("Sam Stewart").alias("PolicyAccountManager"),
        pl.lit("Sam Stewart").alias("Group1Abbrev"),
        (pl.col("Comm").cast(pl.Float64) * 1.5).round(2).cast(pl.Utf8).alias("Comm"))
    revised = pl.concat([df.filter(pl.col("ExpiryDate") < "2026-09-01"),
                         both, fut.slice(30)]).unique(subset=["PolicyID"], keep="first")
    p = tmp_path / "both_changed.csv"
    revised.write_csv(p)

    months = [r[0] for r in rows(conn, """SELECT DISTINCT forecast_month
                                          FROM v_latest_forecast_policy
                                          WHERE forecast_month >= DATE '2026-09-01'""")]
    s = prepare(conn, str(p), "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=months)
    try:
        both_flags = scalar(conn, """SELECT count(*) FROM forecast_movement
                                     WHERE amount_changed AND manager_changed""")
        assert both_flags > 0
        # movement_type alone would report only the amount change.
        assert scalar(conn, """SELECT count(*) FROM forecast_movement
                               WHERE movement_type='manager_changed'""") < both_flags
        # The summary counts by flag, so the transfer is not lost.
        assert scalar(conn, """SELECT SUM(manager_transfers)
                               FROM v_forecast_movement_summary""") >= both_flags
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)
