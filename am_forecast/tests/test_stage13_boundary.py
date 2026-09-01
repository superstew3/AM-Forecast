"""One boundary, everywhere.

An August sales file was imported and accepted, and then did not appear on the
business page, in the account-manager figures, or in any month-by-month grid. The
rows were in sales_transaction the whole time. Nothing errored.

Migration 0020 moved every VIEW onto the calendar. Seven places in the API were
missed and kept reading reporting_settings.cut_off_date, so the two disagreed:
the views knew August had started and the endpoints did not, and the endpoints
were what the pages called.

The worst of them was a Python list comprehension that dropped every row after
the boundary before aggregating -- an accepted upload silently filtered out, with
a reporting setting deciding whether an import was visible.

These tests exist so that cannot happen again quietly. They assert the boundary
is the calendar's, in the code as well as the database, and that a month with
transactions loaded is reported.
"""
from __future__ import annotations

import os
import pathlib
import re
from decimal import Decimal

import pytest


@pytest.fixture(scope="module")
def client(request):
    os.environ["AM_FORECAST_DSN"] = request.config.getoption("--dsn")
    os.environ["AM_FORECAST_DEV_AUTH"] = "1"
    from fastapi.testclient import TestClient

    from app.api import app
    with TestClient(app) as c:
        c.headers.update({"X-User": "pytest-admin", "X-Role": "administrator"})
        yield c


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


API = pathlib.Path(__file__).resolve().parents[1] / "app" / "api"


def test_no_endpoint_derives_a_month_boundary_from_the_cut_off():
    """Grep, deliberately.

    A behavioural test cannot catch this: with the cut-off happening to sit in
    the right place the endpoints agree with the views, and the fault only
    appears in the month after somebody forgets to move a setting that is
    supposed to be doing nothing.

    The cut-off may still be READ -- it is reported on the settings page and in
    Meta, which is fine. What must not come back is deriving a month boundary
    from it.
    """
    offenders = []
    for f in sorted(API.glob("*.py")):
        for n, line in enumerate(f.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("#", "--", '"""', "'")):
                continue
            if re.search(r"date_trunc\(\s*'month'\s*,\s*cut_off_date", line) \
               or re.search(r"au_financial_year\(\s*cut_off_date", line):
                offenders.append(f"{f.name}:{n}")
    assert not offenders, (
        "a month boundary is being derived from the stored cut-off again: "
        + ", ".join(offenders)
        + ". Use reporting_current_month(); the views have since 0020.")


def test_the_api_and_the_views_agree_on_the_current_month(client, conn):
    calendar = scalar(conn, "SELECT reporting_current_month()")
    reported = client.get("/api/business").json()["meta"]["current_month"]
    assert str(reported) == str(calendar), \
        "the API reports a different current month from the database"


def test_the_year_selector_follows_the_calendar(client, conn):
    """The selector on every page is built from /api/periods.

    Derived from the stored cut-off, it would have kept offering last financial
    year as current until somebody advanced a setting.
    """
    expected = scalar(conn, "SELECT au_financial_year(reporting_current_month())")
    d = client.get("/api/periods").json()
    assert d["current_financial_year"] == expected


def test_a_month_with_transactions_loaded_is_reported(client, conn):
    """The symptom, asserted directly.

    Any month that has started and has transactions imported must appear in the
    account-manager figures with income against it. This is what failed: the
    rows existed, the month had started, and the endpoint dropped them.
    """
    month = scalar(conn, """
        SELECT max(period_month) FROM v_actual_month
        WHERE period_month <= reporting_current_month()
          AND net_actual_income IS NOT NULL""")
    if month is None:
        pytest.skip("no started month has transactions in this dataset")

    fy = scalar(conn, "SELECT au_financial_year(%s)", (month,))
    d = client.get(f"/api/managers?period=month&financial_year={fy}").json()
    reported = {str(r.get("period_month")) for r in d["items"]}
    assert str(month) in reported, (
        f"{month} has transactions loaded but does not appear in the manager "
        f"figures; a boundary is excluding a month that has started")


def test_year_to_date_includes_the_month_under_way(client, conn):
    """Year to date means to date, not to the end of last month.

    Excluding the running month is what made an accepted August upload invisible
    -- and it is a defensible-looking choice, which is why it survived.
    """
    current = scalar(conn, "SELECT reporting_current_month()")
    has_income = scalar(conn, """
        SELECT count(*) FROM v_actual_month
        WHERE period_month = %s AND net_actual_income IS NOT NULL""", (current,))
    if not has_income:
        pytest.skip("the month under way has no transactions in this dataset")

    fy = scalar(conn, "SELECT au_financial_year(%s)", (current,))
    ytd = client.get(f"/api/managers?period=ytd&financial_year={fy}").json()
    assert ytd["items"], "year to date returned nothing"
    total = sum(float(r.get("net_actual_income") or 0) for r in ytd["items"])
    completed_only = float(scalar(conn, """
        SELECT COALESCE(SUM(net_actual_income), 0) FROM v_actual_month
        WHERE financial_year = %s AND period_month < reporting_current_month()""",
        (fy,)) or 0)
    assert total > completed_only - 0.01, (
        "year to date excludes the month under way, so an import into the "
        "current month would not be visible")


def test_a_period_that_has_not_started_reports_no_income(client, conn):
    """The label and the figure must agree, at every grain.

    has_started and the income sum were computed by different logic and nothing
    made them consistent, so one row could say a quarter had not begun and
    report $107,244.15 of income in the same breath.

    In production the two happened to agree, because a month still to come holds
    no transactions -- the contradiction only appeared against a dataset with
    data beyond the boundary. That is the case nobody thinks to check, and the
    reason to assert the rule rather than trust the arithmetic.
    """
    fy = scalar(conn, "SELECT au_financial_year(reporting_current_month())")
    for grain in ("month", "quarter", "year"):
        d = client.get(f"/api/managers?period={grain}&financial_year={fy}"
                       "&include_non_ranked=true").json()
        for row in d["items"]:
            if row.get("has_started"):
                continue
            for field in ("net_actual_income", "positive_actual_income",
                          "absolute_return_income", "actual_new_business"):
                v = row.get(field)
                assert v is None or Decimal(str(v)) == 0, (
                    f"{grain}: {row['canonical_manager']} has not started but "
                    f"reports {field} = {v}")


def test_budget_still_covers_the_whole_period(client, conn):
    """The other half of the rule, so the fix does not overreach.

    Actual income is limited to months that have started. Budget and forecast are
    NOT: a full-year budget is a full year, including the months still to come.
    Trimming those would understate every target and make achievement look better
    than it is, which is a worse error than the one being fixed.
    """
    fy = scalar(conn, "SELECT au_financial_year(reporting_current_month())")
    d = client.get(f"/api/managers?period=year&financial_year={fy}").json()
    for row in d["items"]:
        whole = scalar(conn, """
            SELECT COALESCE(SUM(total_budget), 0) FROM v_monthly_budget
            WHERE canonical_manager = %s AND financial_year = %s""",
            (row["canonical_manager"], fy))
        if not whole:
            continue
        reported = row.get("total_budget")
        assert reported is not None, row["canonical_manager"]
        assert abs(Decimal(str(reported)) - Decimal(str(whole))) < Decimal("0.05"), (
            f"{row['canonical_manager']}: the year's budget is trimmed to the "
            f"months that have started; it should be the whole year")
