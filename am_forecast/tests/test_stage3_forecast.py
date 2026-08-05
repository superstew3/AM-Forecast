"""Stage 3 acceptance tests: forecast versioning, movement, budget, outlook.

These build a revised snapshot, accept it, assert, then roll it back, so the
baseline database is unchanged afterwards.
"""
from __future__ import annotations

from decimal import Decimal

import polars as pl
import pytest

from app.importers import accept, prepare, rollback

from conftest import RENEWALS_FILE

CENT = Decimal("0.01")


def future_months(conn) -> list:
    """Months the new snapshot is confirmed to cover.

    Since Stage 4, a month absent from an upload is 'not reported' rather than
    'everything lapsed', so removal comparison only runs for months the uploader
    confirms. Tests that expect removals must declare them.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT forecast_month FROM v_latest_forecast_policy
                       ORDER BY forecast_month""")
        return [r[0] for r in cur.fetchall()]


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


@pytest.fixture
def revised_snapshot(tmp_path):
    """A second snapshot exercising every movement type.

    120 + 120 + 160 policies drop out, 80 change amount, 40 transfer manager,
    60 appear that were not in the original forecast.
    """
    df = pl.read_csv(RENEWALS_FILE, infer_schema_length=0)
    fut = df.filter(pl.col("ExpiryDate") >= "2026-09-01")
    changed = fut.slice(200, 80).with_columns(
        (pl.col("Comm").cast(pl.Float64) * 1.25).round(2).cast(pl.Utf8).alias("Comm"))
    transferred = fut.slice(400, 40).with_columns(
        pl.lit("Sam Stewart").alias("PolicyAccountManager"),
        pl.lit("Sam Stewart").alias("Group1Abbrev"))
    added = fut.slice(300, 60).with_columns(
        (pl.col("PolicyID").cast(pl.Int64) + 500000000).cast(pl.Utf8).alias("PolicyID"))
    revised = pl.concat([fut.slice(120, 80), changed, transferred, fut.slice(600), added])
    out = pl.concat([df.filter(pl.col("ExpiryDate") < "2026-09-01"), revised]) \
            .unique(subset=["PolicyID"], keep="first")
    p = tmp_path / "renewals_revised.csv"
    out.write_csv(p)
    return str(p)


@pytest.fixture
def second_snapshot(conn, revised_snapshot):
    months = future_months(conn)
    s = prepare(conn, revised_snapshot, "pytest")
    result = accept(conn, s.batch_id, "pytest", confirmed_months=months)
    yield result
    rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)


# --- versioning ---------------------------------------------------------------

def test_first_snapshot_records_no_movement(conn):
    """The opening snapshot establishes the baseline; it does not move it.

    Without this the entire book reads as 'added_after_original' and the opening
    position becomes a fictitious gain.
    """
    first = scalar(conn, "SELECT MIN(id) FROM forecast_snapshot")
    assert scalar(conn, """SELECT count(*) FROM forecast_movement
                           WHERE to_snapshot_id = %s""", (first,)) == 0


def test_original_forecast_frozen_after_second_snapshot(conn, revised_snapshot):
    before_total = scalar(conn, "SELECT SUM(forecast_contribution) FROM original_forecast")
    before_count = scalar(conn, "SELECT count(*) FROM original_forecast")
    before_batches = set(rows(conn, "SELECT DISTINCT established_batch_id FROM original_forecast"))

    s = prepare(conn, revised_snapshot, "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=future_months(conn))
    try:
        assert scalar(conn, "SELECT SUM(forecast_contribution) FROM original_forecast") \
            == before_total
        assert scalar(conn, "SELECT count(*) FROM original_forecast") == before_count
        # No rows attributed to the new batch at all.
        assert set(rows(conn, "SELECT DISTINCT established_batch_id FROM original_forecast")) \
            == before_batches
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)


def test_movement_types_are_classified_correctly(conn, second_snapshot):
    counts = dict(rows(conn, """SELECT movement_type, count(*)
                                FROM forecast_movement GROUP BY 1"""))
    assert counts["removed_from_latest"] == 400
    assert counts["amount_changed"] == 80
    assert counts["added_after_original"] == 60
    assert counts["manager_changed"] == 40


def test_removal_never_creates_negative_forecast_income(conn, second_snapshot):
    """Rule 6: a removed policy contributes zero, not a negative."""
    assert scalar(conn, """SELECT count(*) FROM forecast_movement
                           WHERE movement_type='removed_from_latest'
                             AND latest_income <> 0""") == 0
    assert scalar(conn, "SELECT MIN(latest_income) FROM forecast_movement") >= 0


def test_removed_income_stays_visible_as_movement(conn, second_snapshot):
    """Rule 7: removed forecast income is reported, not silently dropped."""
    removed = scalar(conn, """SELECT SUM(previous_income) FROM forecast_movement
                              WHERE movement_type='removed_from_latest'""")
    assert removed > 0
    assert abs(removed - Decimal("85555.52")) <= CENT


def test_movement_reconciles_to_snapshot_difference(conn, second_snapshot):
    """Total movement must equal the difference between the two snapshots."""
    net_movement = scalar(conn, "SELECT SUM(movement_amount) FROM forecast_movement")
    ids = [r[0] for r in rows(conn, "SELECT id FROM forecast_snapshot ORDER BY id")]
    older, newer = ids[-2], ids[-1]
    cut = scalar(conn, """SELECT date_trunc('month', cut_off_date)::date
                          FROM reporting_settings WHERE id=1""")
    diff = scalar(conn, """
        SELECT COALESCE((SELECT SUM(forecast_contribution) FROM forecast_policy
                         WHERE snapshot_id=%s AND NOT is_excluded AND forecast_month > %s), 0)
             - COALESCE((SELECT SUM(forecast_contribution) FROM forecast_policy
                         WHERE snapshot_id=%s AND NOT is_excluded AND forecast_month > %s), 0)
    """, (newer, cut, older, cut))
    assert abs(net_movement - diff) <= CENT


def test_added_policy_has_zero_original_and_positive_latest(conn, second_snapshot):
    """Test 8."""
    orig, latest = rows(conn, """
        SELECT SUM(original_income), SUM(latest_income) FROM forecast_movement
        WHERE movement_type='added_after_original'""")[0]
    assert orig == 0
    assert latest > 0


def test_amount_change_keeps_original_unchanged(conn, second_snapshot):
    """Test 9."""
    for policy_id, original, previous, latest in rows(conn, """
            SELECT policy_id, original_income, previous_income, latest_income
            FROM forecast_movement WHERE movement_type='amount_changed' LIMIT 20"""):
        frozen = scalar(conn, """SELECT forecast_contribution FROM original_forecast
                                 WHERE policy_id=%s AND grain='policy'""", (policy_id,))
        assert frozen == original
        assert latest != previous


def test_manager_transfer_recorded_without_amount_change(conn, second_snapshot):
    from_m, to_m = rows(conn, """SELECT from_manager, to_manager FROM forecast_movement
                                 WHERE movement_type='manager_changed' LIMIT 1""")[0]
    assert from_m != to_m
    assert scalar(conn, """SELECT SUM(movement_amount) FROM forecast_movement
                           WHERE movement_type='manager_changed'""") == 0


# --- latest forecast presentation ---------------------------------------------

def test_completed_month_has_no_latest_forecast(conn):
    """A completed month reports actuals. Its Latest must be N/A, not zero.

    Reporting zero would manufacture a $348k adverse movement for July 2026
    purely because its renewals had already transacted.
    """
    latest, movement, future = rows(conn, """
        SELECT SUM(latest_forecast), SUM(forecast_movement), bool_or(is_future_period)
        FROM v_forecast_position_month WHERE forecast_month = DATE '2026-07-01'""")[0]
    assert future is False
    assert latest is None
    assert movement is None


def test_future_months_carry_a_latest_forecast(conn):
    latest = scalar(conn, """SELECT SUM(latest_forecast) FROM v_forecast_position_month
                             WHERE forecast_month = DATE '2026-08-01'""")
    assert latest is not None and latest > 0


def test_no_monthly_latest_forecast_is_negative(conn, second_snapshot):
    worst = scalar(conn, """SELECT MIN(total) FROM (
        SELECT forecast_month, SUM(latest_forecast) AS total
        FROM v_latest_forecast_month GROUP BY 1) x""")
    assert worst >= 0


# --- budget -------------------------------------------------------------------

def test_budget_does_not_move_when_latest_forecast_moves(conn, revised_snapshot):
    """Rule 25 and test 25: a lapse, removal or forecast fall never rewrites the
    original target."""
    before = scalar(conn, """SELECT SUM(total_budget) FROM v_budget_quarter
                             WHERE financial_year=2026""")
    months = future_months(conn)
    s = prepare(conn, revised_snapshot, "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=months)
    try:
        after = scalar(conn, """SELECT SUM(total_budget) FROM v_budget_quarter
                                WHERE financial_year=2026""")
        assert after == before
        # And the Latest Forecast really did move, so this is not a vacuous pass.
        assert scalar(conn, "SELECT SUM(movement_amount) FROM forecast_movement") < 0
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)


def test_monthly_allocation_sums_to_the_quarterly_target(conn):
    for manager, fy, q, monthly, quarterly in rows(conn, """
            SELECT m.canonical_manager, m.financial_year, m.financial_quarter,
                   SUM(m.calculated_growth_target), MAX(b.new_business_growth_target)
            FROM v_monthly_budget m
            JOIN v_budget_quarter b ON b.canonical_manager = m.canonical_manager
             AND b.financial_year = m.financial_year
             AND b.financial_quarter = m.financial_quarter
            GROUP BY 1,2,3"""):
        assert abs(monthly - quarterly) <= Decimal("0.05"), f"{manager} FY{fy} Q{q}"


def test_allocation_is_forecast_weighted_not_equal(conn):
    """An equal split would over-target December by roughly 91% and under-target
    November by 30%, because the renewal book is materially uneven."""
    result = {m: t for m, t in rows(conn, """
        SELECT forecast_month, SUM(calculated_growth_target) FROM v_monthly_budget
        WHERE financial_year=2026 AND financial_quarter=2 GROUP BY 1""")}
    nov = result[[k for k in result if k.month == 11][0]]
    dec_ = result[[k for k in result if k.month == 12][0]]
    assert nov > dec_ * 2
    assert scalar(conn, """SELECT count(*) FROM v_monthly_budget
                           WHERE allocation_method <> 'forecast_weighted'""") == 0


def test_monthly_override_replaces_calculated_target(conn):
    month = "2026-11-01"
    before = scalar(conn, """SELECT new_business_growth_target FROM v_monthly_budget
                             WHERE canonical_manager='Sam Stewart' AND forecast_month=%s""",
                    (month,))
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO monthly_target_override
            (canonical_manager, target_month, override_amount, reason, created_by, active)
            VALUES ('Sam Stewart', %s, 9999.00, 'test override', 'pytest', true)""", (month,))
        cur.execute("""SELECT new_business_growth_target, is_overridden, override_reason
                       FROM v_monthly_budget
                       WHERE canonical_manager='Sam Stewart' AND forecast_month=%s""", (month,))
        after, overridden, reason = cur.fetchone()
    conn.rollback()
    assert before != Decimal("9999.00")
    assert after == Decimal("9999.00")
    assert overridden is True
    assert reason == "test override"


# --- outlook ------------------------------------------------------------------

def test_outlook_is_completed_actual_plus_future_forecast(conn):
    completed, future, outlook = rows(conn, """
        SELECT SUM(completed_actual), SUM(future_latest_forecast), SUM(latest_outlook)
        FROM v_outlook_quarter WHERE financial_year=2026""")[0]
    assert abs(outlook - (completed + future)) <= CENT


def test_outlook_contains_no_assumed_new_business(conn):
    """Test 27: future periods carry renewal forecast only."""
    assert scalar(conn, """SELECT count(*) FROM v_outlook_month
                           WHERE basis='forecast' AND net_actual_income <> 0""") == 0
    future_total = scalar(conn, """SELECT SUM(latest_forecast) FROM v_outlook_month
                                   WHERE basis='forecast' AND financial_year=2026""")
    forecast_total = scalar(conn, """
        SELECT SUM(latest_forecast) FROM v_latest_forecast_month WHERE financial_year=2026""")
    assert abs(future_total - forecast_total) <= CENT


def test_completed_period_uses_actuals_not_forecast(conn):
    """Rule 10."""
    basis = scalar(conn, """SELECT DISTINCT basis FROM v_outlook_month
                            WHERE month = DATE '2026-07-01'""")
    assert basis == "actual"
    july = scalar(conn, """SELECT SUM(outlook_income) FROM v_outlook_month
                           WHERE month = DATE '2026-07-01'""")
    actual = scalar(conn, """SELECT SUM(net_actual_income) FROM v_actual_month
                             WHERE period_month = DATE '2026-07-01'""")
    assert july == actual


def test_remaining_gap_is_budget_less_outlook(conn):
    for q, budget, outlook, gap in rows(conn, """
            SELECT financial_quarter, SUM(total_budget), SUM(latest_outlook),
                   SUM(remaining_budget_gap)
            FROM v_outlook_quarter WHERE financial_year=2026 GROUP BY 1"""):
        assert abs(gap - (budget - outlook)) <= CENT


# --- rollback of a snapshot ---------------------------------------------------

def test_snapshot_rollback_restores_coverage_to_previous(conn, revised_snapshot):
    """Rolling back a later snapshot must hand Latest back to the earlier one,
    not delete coverage the earlier snapshot still owns."""
    before = dict(rows(conn, """SELECT forecast_month, latest_snapshot_id
                                FROM forecast_month_coverage"""))
    before_orig = dict(rows(conn, """SELECT forecast_month, original_snapshot_id
                                     FROM forecast_month_coverage"""))
    s = prepare(conn, revised_snapshot, "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=future_months(conn))
    rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)

    after = dict(rows(conn, """SELECT forecast_month, latest_snapshot_id
                               FROM forecast_month_coverage"""))
    after_orig = dict(rows(conn, """SELECT forecast_month, original_snapshot_id
                                    FROM forecast_month_coverage"""))
    assert after == before
    assert after_orig == before_orig
    assert scalar(conn, "SELECT count(*) FROM forecast_movement") == 0
    assert scalar(conn, "SELECT count(*) FROM forecast_snapshot") == 1
