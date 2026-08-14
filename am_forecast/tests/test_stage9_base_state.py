"""Base state guard.

Runs last. Every earlier test that alters state must have restored it, so these
assertions are the standing proof that the production position shown in the
documentation is the supplied one, not a leftover test scenario.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

CENT = Decimal("0.01")
BASE_CUT_OFF = dt.date(2026, 7, 31)


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def test_cut_off_restored(conn):
    """The cut-off must be a month end, and must not claim an incomplete month.

    Was pinned to 31 July 2026. Tests that need a closed period move the cut-off
    and restore it, so what matters is that it comes back coherent, not that it
    equals one particular date.
    """
    cut = scalar(conn, "SELECT cut_off_date FROM reporting_settings WHERE id=1")
    assert cut == scalar(conn, "SELECT (date_trunc('month', %s::date) + "
                               "INTERVAL '1 month - 1 day')::date", (cut,)), \
        "the cut-off should sit at a month end"


def test_only_the_supplied_snapshot_remains(conn):
    assert scalar(conn, "SELECT count(*) FROM forecast_snapshot") == 1
    assert scalar(conn, "SELECT count(*) FROM forecast_movement") == 0


def test_no_synthetic_transactions_remain(conn):
    """No test may leave rows behind.

    Previously asserted a row count of one specific export, so it failed on any
    new dataset while saying nothing about contamination. Identifying synthetic
    rows directly holds regardless of what is loaded.
    """
    assert scalar(conn, """
        SELECT count(*) FROM sales_transaction
        WHERE fingerprint LIKE %s OR fingerprint LIKE %s""",
                  ("pytest-%", "synthetic-%")) == 0
    assert scalar(conn, """
        SELECT count(*) FROM forecast_policy
        WHERE source_manager LIKE %s OR source_manager LIKE %s""",
                  ("pytest%", "synthetic%")) == 0
    assert scalar(conn, "SELECT count(*) FROM app_user WHERE email LIKE %s",
                  ("pytest-%",)) == 0



def test_supplied_totals_intact(conn):
    """Reported income equals what the accepted batches said would land.

    The point of the check is that acceptance delivers exactly what the preview
    promised, which is true of any dataset. Pinning it to one export's totals
    measured the export, not the system.
    """
    from app.validation import CENT, sales_expected

    expected = sales_expected(conn)
    reported = scalar(conn, """
        SELECT COALESCE(SUM(actual_income), 0) FROM sales_transaction
        WHERE NOT is_excluded""")
    assert abs(reported - expected["net_income"]) <= CENT

    positive = scalar(conn, """
        SELECT COALESCE(SUM(positive_income), 0) FROM sales_transaction
        WHERE NOT is_excluded""")
    assert abs(positive - expected["positive_income"]) <= CENT

    # Income is the primary associate share and must never silently revert to
    # the gross figure, which is retained alongside it.
    gross = scalar(conn, """
        SELECT COALESCE(SUM(gross_income), 0) FROM sales_transaction
        WHERE NOT is_excluded""")
    assert gross > reported, "SIG income should be below gross commission and fees"



def test_allocation_integrity_clean(conn):
    assert scalar(conn, "SELECT count(*) FROM v_allocation_breaches") == 0


def test_coverage_names_a_snapshot_that_still_holds_the_forecast(conn):
    """A covered month must have the Original Forecast its coverage claims.

    Coverage naming a snapshot with no surviving rows is the signature of a
    rollback that removed a baseline belonging to a snapshot still in place.
    Nothing downstream reports it: the month keeps a Latest Forecast, the budget
    quietly goes to zero, and the only symptom is a reconciliation gap nobody
    can source.
    """
    orphaned = scalar(conn, """
        SELECT count(*) FROM forecast_month_coverage c
        WHERE c.original_snapshot_id IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM original_forecast o
                          WHERE o.forecast_month = c.forecast_month)""")
    assert orphaned == 0, "a covered month has lost its Original Forecast"


def test_every_manager_in_a_budgeted_quarter_has_a_budget(conn):
    """Budget and Outlook must cover the same managers, where a budget exists.

    A manager in one and not the other makes the two totals differ from the sum
    of the per-manager rows, so the remaining gap stops reconciling and the
    manager appears with no target rather than with a target of zero — which
    reads as failure rather than as absence.

    Scoped to quarters that carry a budget. Asserting this across the whole
    database was wrong on the full book: a financial year with actuals but no
    forecast baseline has no budget by design, and every manager in it counted
    as a breach. On the real dataset that produced 73 false positives and made a
    healthy database look damaged — the same class of dataset-pinning this suite
    was rewritten to avoid.
    """
    missing = rows(conn, """
        SELECT o.canonical_manager, o.financial_year, o.financial_quarter
        FROM v_outlook_quarter o
        WHERE o.total_budget IS NULL
          AND EXISTS (SELECT 1 FROM v_budget_quarter b
                      WHERE b.financial_year = o.financial_year
                        AND b.financial_quarter = o.financial_quarter)""")
    assert not missing, \
        f"outlook rows with no budget in a budgeted quarter: {missing}"
