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


def test_cut_off_restored(conn):
    assert scalar(conn, "SELECT cut_off_date FROM reporting_settings WHERE id=1") \
        == BASE_CUT_OFF


def test_only_the_supplied_snapshot_remains(conn):
    assert scalar(conn, "SELECT count(*) FROM forecast_snapshot") == 1
    assert scalar(conn, "SELECT count(*) FROM forecast_movement") == 0


def test_no_synthetic_transactions_remain(conn):
    """Fixture invoices start at 8,800,000."""
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE invoice_number >= 8800000""") == 0
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == 14886


def test_supplied_totals_intact(conn):
    assert abs(scalar(conn, """SELECT SUM(actual_income) FROM sales_transaction
                               WHERE NOT is_excluded""")
               - Decimal("4961376.69")) <= CENT
    assert abs(scalar(conn, """SELECT SUM(forecast_contribution) FROM original_forecast
                               WHERE financial_year=2026""") - Decimal("3701892.60")) <= CENT
    assert abs(scalar(conn, """SELECT SUM(total_budget) FROM v_budget_quarter
                               WHERE financial_year=2026""") - Decimal("3979534.55")) <= CENT


def test_allocation_integrity_clean(conn):
    assert scalar(conn, "SELECT count(*) FROM v_allocation_breaches") == 0
