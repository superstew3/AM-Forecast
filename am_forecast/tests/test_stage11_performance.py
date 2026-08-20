"""The two-ledger model, and the endpoints that had no test at all.

Ten endpoints were unexercised, including /managers/{manager}/detail -- the
most-used page in the application -- and every override route. None of them
would have produced a wrong number on their own; the risk was that a future
change could break one quietly, which is exactly what happened repeatedly
elsewhere in this system before the rules were pinned down.

These assert the RULES rather than one dataset's figures: that a running month
is never scored, that an unimported month is distinguishable from an empty one,
that an override opens one month once. A test written against today's numbers
goes red the day the data changes, which teaches everyone to ignore it.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest


@pytest.fixture(scope="module")
def client(request):
    os.environ["AM_FORECAST_DSN"] = request.config.getoption("--dsn")
    os.environ["AM_FORECAST_DEV_AUTH"] = "1"
    from fastapi.testclient import TestClient

    from app.api import app
    with TestClient(app) as c:
        # Administrator throughout: the override routes are admin-only, and a
        # test that silently ran as a viewer would pass by being refused.
        c.headers.update({"X-User": "pytest-admin", "X-Role": "administrator"})
        yield c


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


# --- the two-ledger model -----------------------------------------------------

def test_performance_returns_both_ledgers(client):
    d = client.get("/api/performance").json()
    assert d["months"], "no month rows at all"
    for m in d["months"]:
        # Both figures always present as keys, even where a value is withheld.
        # The whole point of the model is that one never substitutes for the
        # other, so a row missing a side would mean the substitution is back.
        assert "forecast_income" in m and "target_income" in m
        assert "actual_income" in m


def test_a_running_month_is_never_scored(client):
    """The rule that matters most: a part month is not a result.

    Achievement withheld, status in_progress. Reporting a percentage here put a
    whole month's target against a fortnight's income and made a manager three
    weeks into a quarter read as failing.
    """
    d = client.get("/api/performance").json()
    running = [m for m in d["months"] if m["month_state"] == "in_progress"]
    if not running:
        pytest.skip("no month is under way in this dataset")
    for m in running:
        assert m["achievement_pct"] is None, m
        assert m["status"] in ("in_progress", "missing_forecast",
                               "actuals_not_loaded", "baseline_unverified"), m
        assert m["status"] not in ("achieved", "below_target"), m


def test_a_future_month_reports_nothing_rather_than_zero(client):
    d = client.get("/api/performance").json()
    future = [m for m in d["months"] if m["month_state"] == "future"]
    if not future:
        pytest.skip("this dataset has no month still to come")
    for m in future:
        assert m["actual_income"] is None, m
        assert m["achievement_pct"] is None, m
        assert m["status"] == "not_started", m


def test_an_unimported_month_is_not_an_empty_one(client):
    """$0.00 and "we have not loaded it" must not look the same.

    They are different answers to "how did that month go", and reporting the
    second as the first is how a month nobody uploaded reads as a month where
    nobody earned anything.
    """
    d = client.get("/api/performance").json()

    # Completed months only. For the month still running, "in progress" takes
    # precedence over "actuals not loaded" -- deliberately, and the view checks it
    # first: a running month is not scored whether the transactions are in or
    # not, so its state is the more useful fact. The missing actuals are said in
    # the note instead, which is asserted separately below.
    unloaded = [m for m in d["months"]
                if m["actuals_load_state"] == "none"
                and m["month_state"] == "completed"]
    if not unloaded:
        pytest.skip("every completed month has transactions imported")
    for m in unloaded:
        assert m["actual_income"] is None, m
        assert m["status"] == "actuals_not_loaded", m
        assert m["status_note"], "an unavailable figure must say why"


def test_a_running_month_says_how_far_its_actuals_go(client):
    """The month under way is labelled in progress, and explains itself.

    Whether its transactions are loaded, partly loaded or absent, the status stays
    in_progress -- but the note has to carry the difference, or a reader cannot
    tell a month with three weeks of income from one with none.
    """
    d = client.get("/api/performance").json()
    running = [m for m in d["months"] if m["month_state"] == "in_progress"]
    if not running:
        pytest.skip("no month is under way in this dataset")
    for m in running:
        assert m["status"] == "in_progress" or m["status"] in (
            "missing_forecast", "baseline_unverified"), m
        if m["status"] == "in_progress":
            assert m["status_note"], "a running month must say how far its actuals go"
            assert "in progress" in m["status_note"].lower(), m["status_note"]


def test_target_is_the_forecast_plus_growth(client):
    d = client.get("/api/performance").json()
    checked = 0
    for m in d["months"]:
        if not m["forecast_income"] or not m["uplift_applied"]:
            continue
        expected = Decimal(str(m["forecast_income"])) * Decimal(str(m["uplift_applied"]))
        assert abs(Decimal(str(m["target_income"])) - expected) < Decimal("0.05"), m
        checked += 1
    if not checked:
        pytest.skip("no month carries both a forecast and an uplift")


def test_quarter_achievement_counts_closed_months_only(client):
    d = client.get("/api/performance").json()
    for q in d["quarters"]:
        if q["achievement_pct_completed"] is None:
            continue
        # Scored against the closed months' target, never the whole quarter's.
        assert Decimal(str(q["target_income_scoreable"])) <= \
            Decimal(str(q["target_income"])) + Decimal("0.01"), q


def test_performance_months_rolls_up_the_same_figures(client):
    """The summary must be the detail added up, or one of them is wrong."""
    detail = client.get("/api/performance").json()["months"]
    rolled = client.get("/api/performance/months").json()["months"]
    by_month: dict = {}
    for m in detail:
        by_month.setdefault(m["month"], Decimal(0))
        by_month[m["month"]] += Decimal(str(m["target_income"] or 0))
    for r in rolled:
        assert abs(Decimal(str(r["target_income"] or 0))
                   - by_month.get(r["month"], Decimal(0))) < Decimal("0.05"), r


def test_month_state_follows_the_calendar(conn):
    """Assert the boundary itself, not only its consequences.

    The tests above skip when no month is under way -- correct, since a rule the
    data cannot exercise should not be asserted. But that means breaking
    month_state so that nothing is ever in progress makes them all skip and pass.
    Checked directly: a month before this one is completed, this one is in
    progress, the next is future. Verified by deliberately breaking the function
    and confirming this test goes red.
    """
    this_month = scalar(conn, "SELECT reporting_current_month()")
    prev = scalar(conn, "SELECT (%s::date - INTERVAL '1 month')::date", (this_month,))
    nxt = scalar(conn, "SELECT (%s::date + INTERVAL '1 month')::date", (this_month,))

    assert scalar(conn, "SELECT month_state(%s)", (prev,)) == "completed"
    assert scalar(conn, "SELECT month_state(%s)", (this_month,)) == "in_progress"
    assert scalar(conn, "SELECT month_state(%s)", (nxt,)) == "future"

    # And the boundary the importer enforces agrees with it: this month closed
    # to uploads, the next one open. A view and a function disagreeing about
    # exactly this once shipped, and nothing caught it.
    assert scalar(conn, "SELECT forecast_month_is_open(%s)", (this_month,)) is False
    assert scalar(conn, "SELECT forecast_month_is_open(%s)", (nxt,)) is True


# --- the override, which had no test at all -----------------------------------

def test_override_refuses_a_month_that_needs_none(client):
    d = client.get("/api/forecast-months/status").json()
    future = [m for m in d["months"] if m["open_to_upload"]]
    if not future:
        pytest.skip("no month is open to upload in this dataset")
    r = client.post("/api/forecast-months/override",
                    json={"forecast_month": str(future[0]["forecast_month"]),
                          "reason": "should be refused, month is already open"})
    assert r.status_code == 400
    assert "no override is needed" in r.json()["detail"].lower()


def test_override_opens_one_month_once(client, conn):
    """Single use, and it must actually unlock the month.

    A standing exemption would quietly become the rule and the month would drift
    on every upload, which is the behaviour the lock exists to stop.
    """
    d = client.get("/api/forecast-months/status").json()
    closed = [m for m in d["months"]
              if not m["open_to_upload"] and not m["override_pending"]]
    if not closed:
        pytest.skip("every month is already open or already overridden")
    month = str(closed[0]["forecast_month"])
    try:
        assert scalar(conn, "SELECT forecast_month_writable(%s)", (month,)) is False

        r = client.post("/api/forecast-months/override",
                        json={"forecast_month": month,
                              "reason": "test: reopening a closed month"})
        assert r.status_code == 200, r.json()
        conn.commit()
        assert scalar(conn, "SELECT forecast_month_writable(%s)", (month,)) is True

        again = client.post("/api/forecast-months/override",
                            json={"forecast_month": month,
                                  "reason": "test: a second grant must be refused"})
        assert again.status_code == 409

        hist = client.get("/api/forecast-months/override/history").json()
        assert any(str(o["forecast_month"]) == month for o in hist["overrides"])
    finally:
        client.delete(f"/api/forecast-months/override/{month}")
        with conn.cursor() as cur:
            cur.execute("""DELETE FROM forecast_month_override
                           WHERE forecast_month = %s AND consumed_at IS NULL""",
                        (month,))
        conn.commit()


# --- manager detail, the most-used page and previously untested ----------------

def test_manager_detail_answers_for_a_real_manager(client, conn):
    who = scalar(conn, """SELECT canonical_manager FROM reporting_manager
                          WHERE include_in_rankings ORDER BY canonical_manager LIMIT 1""")
    if not who:
        pytest.skip("no ranked manager in this dataset")
    d = client.get(f"/api/managers/{who}/detail").json()
    assert d["canonical_manager"] == who
    assert d["months"], "a manager detail with no months is not a detail"
    assert d["rows"], "no measures returned"
    # Every month is labelled, so the grid can tell "not started" from "no data".
    assert len(d["month_status"]) == len(d["months"])


def test_manager_detail_rejects_an_unknown_manager(client):
    r = client.get("/api/managers/Nobody%20At%20All/detail")
    assert r.status_code == 404


def test_the_financial_year_defaults_to_the_calendar(client, conn):
    """No year supplied must mean the year we are actually in.

    Every endpoint defaulted to a literal 2026, which would have served last
    year's figures to anyone who did not pick a year from 1 July 2027 onward,
    with nothing on screen to say so.
    """
    expected = scalar(conn, """SELECT au_financial_year(
        (now() AT TIME ZONE 'Australia/Melbourne')::date)""")
    for path in ("/api/business", "/api/bonus", "/api/budget",
                 "/api/new-business", "/api/performance"):
        d = client.get(path).json()
        got = d.get("financial_year") or d.get("meta", {}).get("financial_year")
        assert got == expected, f"{path} defaulted to {got}, not {expected}"
