"""Stage 3 acceptance tests: forecast versioning, movement, budget, outlook.

These build a revised snapshot, accept it, assert, then roll it back, so the
baseline database is unchanged afterwards.
"""
from __future__ import annotations

from decimal import Decimal

import polars as pl
import datetime as dt

import pytest

from conftest import RENEWALS_FILE

from app.importers import accept, prepare, rollback

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


def completed_month(conn):
    """A completed month that actually holds data.

    The cut-off month is complete by definition, but a mid-month export can
    leave it empty — its actuals and forecast both sit in the month after. Where
    that happens there is no completed period to assert on, and skipping is
    honest; asserting against None is not.
    """
    month = scalar(conn, """
        SELECT MAX(period_month) FROM v_actual_month
        WHERE period_month <= (SELECT date_trunc('month', cut_off_date)::date
                               FROM reporting_settings WHERE id = 1)""")
    if month is None:
        pytest.skip("this dataset has no completed month holding actuals")
    return month


def next_month(conn):
    """The first month that has not started, in Melbourne.

    Was the month after the stored cut-off. Once the views moved to the calendar
    the two diverged -- with a cut-off of 31 July this returned August, which the
    calendar correctly reports as already under way, so tests looking for "a
    future month" were handed the current one and found no forecast in it.
    """
    return scalar(conn, """
        SELECT (reporting_current_month() + INTERVAL '1 month')::date""")


def held_and_open_months(conn):
    """Months whose Original Forecast is held, and months a newer upload takes.

    Asks the database rather than restating the rule. This used to derive the
    split from the reporting cut-off, which stopped matching the importer the
    moment the importer moved to the calendar month -- the tests then insisted a
    month was open while the accept path correctly refused to write it, and three
    of them failed against correct behaviour.

    A duplicated rule is the same defect that put a hand-written copy of the
    exclusion rules in the pin script and left it wrong by $640 for months.
    forecast_month_is_open() is the one implementation; everything else asks it.
    """
    held = [m for (m,) in rows(conn, """
        SELECT DISTINCT o.forecast_month FROM original_forecast o
        WHERE NOT forecast_month_is_open(o.forecast_month)
        ORDER BY 1""")]
    still_open = [m for (m,) in rows(conn, """
        SELECT DISTINCT o.forecast_month FROM original_forecast o
        WHERE forecast_month_is_open(o.forecast_month)
        ORDER BY 1""")]
    return held, still_open


@pytest.fixture
def revised_snapshot(conn, tmp_path):
    """A second snapshot exercising every movement type.

    Sized as fractions of the file rather than fixed counts. The original used
    absolute slices (200, 400, 600) that fell off the end of a smaller export,
    producing empty blocks — so the movement types under test never occurred and
    the assertions had nothing to work on.

    Future months only, because a completed month keeps its baseline and the
    point here is movement in months still open.
    """
    # The first month after the reporting cut-off: months up to and including
    # the cut-off are complete and keep their baseline, so movement can only be
    # exercised beyond it.
    boundary = scalar(conn, """
        SELECT (date_trunc('month', cut_off_date) + INTERVAL '1 month')::date
        FROM reporting_settings WHERE id = 1""").isoformat()

    df = pl.read_csv(RENEWALS_FILE, infer_schema_length=0)
    fut = df.filter(pl.col("ExpiryDate") >= boundary)
    if len(fut) < 20:
        pytest.skip("too few future policies in this dataset to exercise movement")

    n = len(fut)
    block = max(1, n // 8)
    changed = fut.slice(0, block).with_columns(
        (pl.col("PrimaryAssocCommSum").cast(pl.Float64) * 1.25)
        .round(2).cast(pl.Utf8).alias("PrimaryAssocCommSum"))
    transferred = fut.slice(block, block).with_columns(
        pl.lit("Sam Stewart").alias("PolicyAccountManager"),
        pl.lit("Sam Stewart").alias("Group1Abbrev"))
    added = fut.slice(2 * block, block).with_columns(
        (pl.col("PolicyID").cast(pl.Int64) + 500000000).cast(pl.Utf8).alias("PolicyID"))
    # Everything from 4 blocks on is retained unchanged; blocks 3 and 4 are
    # omitted entirely, which is what produces the removals.
    retained = fut.slice(4 * block)
    revised = pl.concat([changed, transferred, retained, added])

    out = pl.concat([df.filter(pl.col("ExpiryDate") < boundary), revised]) \
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


def test_second_snapshot_holds_closed_months_and_updates_open_ones(conn, revised_snapshot):
    """Migration 0017's rule, which replaced 'the Original Forecast never moves'.

    This previously asserted the blanket freeze, so a deliberate change read as a
    defect. What must hold now is narrower and stronger: a closed or pinned month
    is untouched down to the batch that established it, and an open month is
    taken over wholesale by the newer snapshot rather than accumulating a second
    set of rows beside the old.
    """
    held, still_open = held_and_open_months(conn)
    if not held or not still_open:
        pytest.skip("this dataset has no held month and open month to tell apart")

    def by_month(c):
        return {m: (n, total, batches) for m, n, total, batches in rows(c, """
            SELECT forecast_month, count(*), COALESCE(SUM(forecast_contribution), 0),
                   array_agg(DISTINCT established_batch_id)
            FROM original_forecast GROUP BY 1""")}

    before = by_month(conn)
    s = prepare(conn, revised_snapshot, "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=future_months(conn))
    try:
        after = by_month(conn)
        for m in held:
            assert after[m] == before[m], f"{m} is held and must not have moved"
        for m in still_open:
            count, total, batches = after[m]
            assert batches == [s.batch_id], \
                f"{m} is open and its Original should carry the new snapshot alone"
            assert total == scalar(conn, """
                SELECT COALESCE(SUM(p.forecast_contribution), 0)
                FROM forecast_policy p
                JOIN forecast_snapshot fs ON fs.id = p.snapshot_id
                WHERE fs.batch_id = %s AND NOT p.is_excluded
                  AND p.forecast_month = %s""", (s.batch_id, m)), \
                f"{m} should equal what the new snapshot forecasts for it"
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)


def test_movement_types_are_classified_correctly(conn, second_snapshot):
    """Every kind of change between snapshots is recognised and labelled.

    The counts were fixed at 80, 60 and 40, which belonged to the fixture's old
    absolute slice sizes. The rule is that each movement type occurs and is
    classified, not that a particular number of them do.
    """
    counts = dict(rows(conn, """SELECT movement_type, count(*)
                                FROM forecast_movement GROUP BY 1"""))
    for kind in ("removed_from_latest", "amount_changed",
                 "added_after_original", "manager_changed"):
        assert counts.get(kind, 0) > 0, f"no {kind} movement was produced"
    # 'unchanged' is a legitimate classification: a policy present in both
    # snapshots with no change is still accounted for rather than ignored.
    assert set(counts) <= {"removed_from_latest", "amount_changed",
                           "added_after_original", "manager_changed",
                           "unchanged"}, counts

def test_removal_never_creates_negative_forecast_income(conn, second_snapshot):
    """Rule 6: a removed policy contributes zero, not a negative."""
    assert scalar(conn, """SELECT count(*) FROM forecast_movement
                           WHERE movement_type='removed_from_latest'
                             AND latest_income <> 0""") == 0
    assert scalar(conn, "SELECT MIN(latest_income) FROM forecast_movement") >= 0


def test_removed_income_stays_visible_as_movement(conn, second_snapshot):
    """Rule 7: removed forecast income is reported, not silently dropped."""
    removed, rows_removed = rows(conn, """
        SELECT COALESCE(SUM(previous_income), 0), count(*) FROM forecast_movement
        WHERE movement_type='removed_from_latest'""")[0]
    assert rows_removed > 0
    assert removed > 0, "removed income must remain visible as a movement"
    # Every removed row must carry the income it took away.
    assert scalar(conn, """SELECT count(*) FROM forecast_movement
                           WHERE movement_type='removed_from_latest'
                             AND previous_income IS NULL""") == 0

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


def test_added_policy_original_follows_the_state_of_its_month(conn, second_snapshot):
    """Test 8, restated for the closed/open rule.

    An added policy always lifts Latest. Whether it also sits in the Original now
    depends on the month: a held month keeps the baseline it was measured
    against, so the policy is an addition to it and its Original is zero; an open
    month has been re-established from the newer snapshot, so the policy is in
    that Original and the two figures agree. The old blanket zero was written
    when no Original ever moved, and it now fails on correct data.

    Worth flagging rather than silently encoding: 'added_after_original' is a
    misnomer for an open month, where the addition is against the previous
    snapshot rather than against the baseline. The classification is accurate;
    only the name reads oddly.
    """
    added = rows(conn, """
        SELECT forecast_month, original_income, latest_income
        FROM forecast_movement WHERE movement_type = 'added_after_original'""")
    if not added:
        pytest.skip("the revised snapshot added no policies to compare")
    assert sum(latest for _, _, latest in added) > 0

    held, _ = held_and_open_months(conn)
    for month, original, latest in added:
        if month in held:
            assert original == 0, f"{month} is held; an addition is not in its baseline"
        else:
            assert original == latest, f"{month} is open; its baseline includes the addition"


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
    month = completed_month(conn)
    covered = scalar(conn, """SELECT count(*) FROM v_forecast_position_month
                              WHERE forecast_month = %s""", (month,))
    if not covered:
        # A completed month with no forecast position row at all. The aggregate
        # below still returns one row -- of nulls -- so bool_or gives None and
        # the assertion reads it as a failure rather than an absence.
        #
        # It happens whenever the forecast range and the calendar do not overlap:
        # here the extract begins in September while the last completed month is
        # July, so July has actuals and no forecast row was ever created for it.
        # There is nothing to check, which is different from something being
        # wrong.
        pytest.skip(f"{month} has no forecast position row: the forecast in this "
                    f"dataset starts after the last completed month")

    latest, movement, future = rows(conn, """
        SELECT SUM(latest_forecast), SUM(forecast_movement), bool_or(is_future_period)
        FROM v_forecast_position_month WHERE forecast_month = %s""", (month,))[0]
    assert future is False
    assert latest is None
    assert movement is None


def test_future_months_carry_a_latest_forecast(conn):
    m = next_month(conn)
    latest = scalar(conn, """SELECT SUM(latest_forecast) FROM v_forecast_position_month
                             WHERE forecast_month = %s""", (m,))
    if latest is None:
        pytest.skip(f"this dataset carries no forecast for {m}, the first month "
                    f"that has not started")
    assert latest > 0


def test_no_monthly_latest_forecast_is_negative(conn, second_snapshot):
    worst = scalar(conn, """SELECT MIN(total) FROM (
        SELECT forecast_month, SUM(latest_forecast) AS total
        FROM v_latest_forecast_month GROUP BY 1) x""")
    assert worst >= 0


# --- budget -------------------------------------------------------------------

def test_budget_of_a_held_month_does_not_move_when_the_forecast_does(conn, revised_snapshot):
    """Rule 25, scoped to the months it still governs.

    A lapse, removal or forecast fall must never rewrite a target somebody has
    already been measured against — that is the point of the rule, and it holds
    for every closed and pinned month. It cannot hold for an open month, whose
    baseline the newer snapshot is meant to replace; asserting it across the
    whole year made the 0017 change look like a budget defect.
    """
    held, still_open = held_and_open_months(conn)
    if not held:
        pytest.skip("this dataset has no closed or pinned month to hold")
    if not still_open:
        pytest.skip("every month in this dataset is already held, so there is "
                    "nothing a new upload could move")

    def budget_by_month(c):
        return {m: t for m, t in rows(c, """
            SELECT forecast_month, COALESCE(SUM(total_budget), 0)
            FROM v_monthly_budget GROUP BY 1""")}

    before = budget_by_month(conn)
    s = prepare(conn, revised_snapshot, "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=future_months(conn))
    try:
        after = budget_by_month(conn)
        for m in held:
            assert after[m] == before[m], f"{m} is held; its target must not move"
        # And the Latest Forecast really did move, so this is not a vacuous pass.
        assert scalar(conn, "SELECT SUM(movement_amount) FROM forecast_movement") < 0
        assert any(after[m] != before[m] for m in still_open), \
            "no open month moved, so the held months prove nothing"
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
    """The growth target tracks each month's own forecast, not a flat share.

    An equal split would over-target a light month and under-target a heavy one,
    because the renewal book is materially uneven. Previously asserted on two
    named months of one dataset; now on the arithmetic, wherever there is data.
    """
    checked = 0
    for month, forecast, target, pct in rows(conn, """
            SELECT forecast_month, original_forecast, calculated_growth_target,
                   growth_pct
            FROM v_monthly_budget WHERE growth_pct IS NOT NULL"""):
        assert abs(target - forecast * pct) < Decimal("0.01"), month
        checked += 1
    if not checked:
        pytest.skip("no monthly budget rows in this dataset")

    # Where a quarter holds months of different sizes, their targets must
    # differ in the same proportion.
    uneven = rows(conn, """
        SELECT financial_year, financial_quarter,
               MIN(original_forecast), MAX(original_forecast),
               MIN(calculated_growth_target), MAX(calculated_growth_target)
        FROM v_monthly_budget WHERE growth_pct IS NOT NULL
        GROUP BY 1, 2 HAVING COUNT(*) > 1 AND MIN(original_forecast)
                             <> MAX(original_forecast)""")
    for _, _, min_f, max_f, min_t, max_t in uneven:
        assert max_t > min_t, "an uneven month must carry an uneven target"

def test_monthly_override_replaces_calculated_target(conn):
    month = next_month(conn).isoformat()
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
        row = cur.fetchone()
    conn.rollback()
    if row is None:
        pytest.skip(f"this dataset carries no budget for {month}, the first "
                    f"month that has not started, so an override has nothing "
                    f"to replace")
    after, overridden, reason = row
    assert before != Decimal("9999.00")
    assert after == Decimal("9999.00")
    assert overridden is True
    assert reason == "test override"


# --- outlook ------------------------------------------------------------------

def test_outlook_is_completed_actual_plus_future_forecast(conn):
    """Outlook is actuals for closed months plus forecast for the rest."""
    fy = scalar(conn, """SELECT au_financial_year(cut_off_date)
                         FROM reporting_settings WHERE id = 1""")
    result = rows(conn, """
        SELECT COALESCE(SUM(completed_actual), 0),
               COALESCE(SUM(future_latest_forecast), 0),
               COALESCE(SUM(latest_outlook), 0)
        FROM v_outlook_quarter WHERE financial_year = %s""", (fy,))
    if not result:
        pytest.skip("no outlook rows for the current financial year")
    completed, future, outlook = result[0]
    if outlook is None:
        pytest.skip("no outlook figures for this financial year")
    assert abs(outlook - (completed + future)) <= CENT

def test_outlook_contains_no_assumed_new_business(conn):
    """Test 27: future periods carry renewal forecast only."""
    assert scalar(conn, """SELECT count(*) FROM v_outlook_month
                           WHERE basis='forecast' AND net_actual_income <> 0""") == 0
    # Only the forecast side of the outlook, so a dataset whose every month has
    # already started has nothing here. That is not a failure: it means no month
    # is being carried on forecast, which is exactly what the first assertion
    # above is checking for.
    future_total = scalar(conn, """SELECT SUM(latest_forecast) FROM v_outlook_month
                                   WHERE basis='forecast' AND financial_year=2026""")
    if future_total is None:
        pytest.skip("no month in this dataset is still carried on forecast")
    forecast_total = scalar(conn, """
        SELECT COALESCE(SUM(latest_forecast), 0) FROM v_latest_forecast_month
        WHERE financial_year = 2026
          AND forecast_month > reporting_current_month()""")
    assert abs(future_total - forecast_total) <= CENT


def test_completed_period_uses_actuals_not_forecast(conn):
    """Rule 10."""
    basis = scalar(conn, """SELECT DISTINCT basis FROM v_outlook_month
                            WHERE month = %s""", (completed_month(conn),))
    assert basis == "actual"
    july = scalar(conn, """SELECT SUM(outlook_income) FROM v_outlook_month
                           WHERE month = %s""", (completed_month(conn),))
    actual = scalar(conn, """SELECT SUM(net_actual_income) FROM v_actual_month
                             WHERE period_month = %s""", (completed_month(conn),))
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
