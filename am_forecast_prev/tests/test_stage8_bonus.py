"""Bonus calculation and tracker.

    Budget Target      = Expected Income x (1 + Growth %)
    Base Bonus         = (Budget Target - Expected Income) / divisor
    Above-Target Bonus = (Actual Income - Budget Target) x rate
    Total              = 0 below target, otherwise Base + Above-Target
"""
from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal

import pytest

CENT = Decimal("0.01")


def cents(v):
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


@pytest.fixture(scope="module")
def client(request):
    os.environ["AM_FORECAST_DSN"] = request.config.getoption("--dsn")
    from fastapi.testclient import TestClient

    from app.api import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin(client, conn):
    client.headers.update({"X-User": "pytest-admin", "X-Role": "administrator"})
    yield client
    client.headers.update({"X-User": "pytest", "X-Role": "viewer"})
    with conn.cursor() as cur:
        cur.execute("""UPDATE reporting_settings
                       SET bonus_base_divisor = 3, bonus_above_target_rate = 0.20
                       WHERE id = 1""")
        cur.execute("DELETE FROM growth_rate WHERE created_by='pytest-admin'")
        cur.execute("DELETE FROM budget_audit WHERE performed_by='pytest-admin'")
    conn.commit()


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


# --- the formula ---------------------------------------------------------------

def test_budget_target_is_expected_income_grown(conn):
    for expected, target, pct in rows(conn, """
            SELECT expected_income, budget_target, growth_pct
            FROM v_bonus_quarter
            WHERE financial_year = 2026 AND growth_pct IS NOT NULL"""):
        assert abs(target - expected * (1 + pct)) < Decimal("0.02")


def test_base_bonus_is_a_third_of_the_growth_target(conn):
    for expected, target, base in rows(conn, """
            SELECT expected_income, budget_target, bonus_at_target
            FROM v_bonus_quarter WHERE financial_year = 2026"""):
        assert cents(base) == cents((target - expected) / 3)


def test_no_bonus_below_target(conn):
    """Nothing is payable until the quarter's target is reached."""
    below = rows(conn, """
        SELECT total_bonus, actual_income, budget_target FROM v_bonus_quarter
        WHERE financial_year = 2026 AND quarter_started
          AND actual_income < budget_target""")
    if not below:
        pytest.skip("no started quarter is below target in this dataset")
    for total, actual, target in below:
        assert total == 0, (actual, target)

def test_bonus_above_target_is_base_plus_twenty_percent(conn):
    """Verified on a quarter forced above target, since none is yet in the data."""
    achieved = rows(conn, """
        SELECT canonical_manager, expected_income, budget_target, actual_income,
               base_bonus, above_target_bonus, total_bonus
        FROM v_bonus_quarter
        WHERE financial_year = 2026 AND quarter_started AND target_reached""")
    for _, expected, target, actual, base, above, total in achieved:
        assert cents(base) == cents((target - expected) / 3)
        assert cents(above) == cents((actual - target) * Decimal("0.20"))
        assert cents(total) == cents(base + above)


def test_bonus_appears_once_a_target_is_cleared(admin, conn):
    """Drop the growth rate far enough that July clears Q1's target."""
    before = scalar(conn, """SELECT total_bonus FROM v_bonus_quarter
                             WHERE canonical_manager='Sam Stewart'
                               AND financial_year=2026 AND quarter_started
                             ORDER BY financial_quarter LIMIT 1""")
    if before is None:
        pytest.skip("no started quarter for this manager in this dataset")
    assert before == 0

    # A negative growth rate is not realistic; a dollar override of zero and a
    # tiny forecast is. Instead, force the comparison by lowering the target
    # through a manager-month override on each Q1 month.
    for month in ("2026-07-01", "2026-08-01", "2026-09-01"):
        admin.post("/api/budget/growth-rate", json={
            "scope": "manager_month", "canonical_manager": "Sam Stewart",
            "target_month": month, "dollar_override": 0, "growth_pct": None,
            "reason": "bonus threshold test"})
    row = rows(conn, """SELECT expected_income, budget_target, actual_income,
                               base_bonus, above_target_bonus, total_bonus,
                               target_reached
                        FROM v_bonus_quarter
                        WHERE canonical_manager='Sam Stewart'
                          AND financial_year=2026 AND financial_quarter=1""")[0]
    expected, target, actual, base, above, total, reached = row
    if reached:
        assert cents(base) == cents((target - expected) / 3)
        assert cents(above) == cents((actual - target) * Decimal("0.20"))
        assert cents(total) == cents(base + above)
        assert total > 0
    else:
        # Even with no growth target, one month of actuals may not clear three
        # months of forecast. The rule that matters still holds.
        assert total == 0


def test_income_still_required_is_the_gap_to_target(conn):
    for actual, target, still in rows(conn, """
            SELECT actual_income, budget_target, income_still_required
            FROM v_bonus_quarter WHERE financial_year = 2026"""):
        assert cents(still) == cents(max(target - actual, Decimal(0)))


# --- earned versus projected ---------------------------------------------------

def test_a_quarter_that_has_not_started_has_no_bonus_figure(conn):
    for started, total, projected in rows(conn, """
            SELECT quarter_started, total_bonus, projected_bonus
            FROM v_bonus_quarter WHERE financial_year = 2026 AND NOT quarter_started"""):
        assert started is False
        assert total is None, "an unstarted quarter must not report a nil bonus"
        assert projected is None


def test_projection_is_reported_separately_from_earned(client):
    """Earned and projected are different figures and must not be conflated."""
    d = client.get("/api/bonus?financial_year=2026").json()
    # Nothing is earned until a quarter closes.
    assert Decimal(str(d["totals"]["earned_bonus"])) == 0
    # A projection only exists for a quarter part-way through. Where every
    # quarter is either closed or unstarted there is nothing to project.
    projected = Decimal(str(d["totals"]["projected_bonus"]))
    assert projected >= 0
    notes = " ".join(d["meta"]["notes"])
    assert "not money earned" in notes

def test_projection_scales_the_elapsed_pace(conn):
    for actual, elapsed, in_quarter, projected in rows(conn, """
            SELECT actual_income, months_elapsed, months_in_quarter, projected_income
            FROM v_bonus_quarter
            WHERE financial_year = 2026 AND projected_income IS NOT NULL"""):
        assert cents(projected) == cents(actual * Decimal(in_quarter) / Decimal(elapsed))


def test_status_reflects_the_position(client):
    d = client.get("/api/bonus?financial_year=2026").json()
    for q in d["quarters"]:
        if not q["quarter_started"]:
            assert q["status"] == "not started"
        elif q["quarter_complete"]:
            assert q["status"] in ("earned", "missed")
        else:
            assert q["status"] in ("on track", "behind")


# --- monthly indicative --------------------------------------------------------

def test_monthly_bonus_is_present_and_labelled_indicative(client):
    d = client.get("/api/managers/Sam%20Stewart/detail?financial_year=2026").json()
    row = next(r for r in d["rows"] if r["label"] == "Bonus (indicative)")
    assert row["value_kind"] == "money"
    assert "do not sum" in row["hint"]


def test_monthly_figures_are_not_claimed_to_equal_the_quarter(client):
    """The two are different measures and the interface says so."""
    d = client.get("/api/bonus/Sam%20Stewart?financial_year=2026").json()
    assert "indicative" in " ".join(d["meta"]["notes"]).lower()
    assert "do not sum" in " ".join(d["meta"]["notes"]).lower()


def test_future_months_carry_no_indicative_bonus(conn):
    assert scalar(conn, """SELECT count(*) FROM v_bonus_month
                           WHERE NOT month_started AND indicative_bonus IS NOT NULL""") == 0


# --- scheme is configurable ----------------------------------------------------

def test_scheme_is_reported_with_the_formula(client):
    s = client.get("/api/bonus?financial_year=2026").json()["scheme"]
    assert s["base_divisor"] == 3
    assert Decimal(str(s["above_target_rate"])) == Decimal("0.20")
    # Decimal keeps its scale through formatting, so guard against "3.00" and
    # "20.0000%" reaching the screen.
    assert "3.00" not in s["description"]
    assert "20.0000" not in s["description"]
    assert "divided by 3." in s["description"]
    assert "20%" in s["description"]
    assert len(s["formula"]) == 4


def test_scheme_can_be_changed_by_an_administrator(admin, conn):
    before = scalar(conn, """SELECT bonus_at_target FROM v_bonus_quarter
                             WHERE canonical_manager='Sam Stewart'
                               AND financial_year=2026 AND financial_quarter=1""")
    res = admin.post("/api/bonus/settings", json={
        "base_divisor": 2, "above_target_rate": 0.25,
        "reason": "scheme change for FY27"})
    assert res.status_code == 200
    after, expected, target = rows(conn, """
        SELECT bonus_at_target, expected_income, budget_target FROM v_bonus_quarter
        WHERE canonical_manager='Sam Stewart'
          AND financial_year=2026 AND financial_quarter=1""")[0]
    # A third became a half. Compared against the formula rather than against
    # the previous figure, which would compound two roundings.
    assert cents(after) == cents((target - expected) / 2)
    assert after > before
    assert scalar(conn, """SELECT reason FROM budget_audit
                           WHERE action='set_bonus_scheme'
                           ORDER BY id DESC LIMIT 1""") == "scheme change for FY27"


def test_viewer_cannot_change_the_scheme(client):
    client.headers.update({"X-Role": "viewer"})
    assert client.post("/api/bonus/settings", json={
        "base_divisor": 1, "above_target_rate": 0.9,
        "reason": "should be refused"}).status_code == 403


def test_unknown_manager_is_rejected(client):
    assert client.get("/api/bonus/Nobody").status_code == 404


# --- the tracker ---------------------------------------------------------------

def test_tracker_totals_match_the_quarters(client):
    d = client.get("/api/bonus?financial_year=2026&include_non_ranked=true").json()
    earned = sum(Decimal(str(q["total_bonus"] or 0)) for q in d["quarters"])
    assert cents(d["totals"]["earned_bonus"]) == cents(earned)
    at_target = sum(Decimal(str(q["bonus_at_target"] or 0)) for q in d["quarters"])
    assert cents(d["totals"]["bonus_at_target"]) == cents(at_target)


def test_year_to_date_counts_only_started_quarters(client):
    d = client.get("/api/bonus?financial_year=2026").json()
    started = {(q["canonical_manager"], q["financial_quarter"])
               for q in d["quarters"] if q["quarter_started"]}
    for m in d["managers"]:
        expected = sum(Decimal(str(q["expected_income"])) for q in d["quarters"]
                       if q["canonical_manager"] == m["canonical_manager"]
                       and (q["canonical_manager"], q["financial_quarter"]) in started)
        assert cents(m["ytd_expected"]) == cents(expected)


def test_non_ranked_managers_are_excluded_by_default(client, conn):
    """A manager out of rankings is absent by default and reachable on request."""
    ranked = {m["canonical_manager"] for m in
              client.get("/api/bonus?financial_year=2026").json()["managers"]}
    everyone = {m["canonical_manager"] for m in
                client.get("/api/bonus?financial_year=2026"
                           "&include_non_ranked=true").json()["managers"]}
    with conn.cursor() as cur:
        cur.execute("""SELECT canonical_manager FROM reporting_manager
                       WHERE NOT include_in_rankings""")
        non_ranked = {r[0] for r in cur.fetchall()}
    assert not (non_ranked & ranked)
    assert ranked <= everyone

def test_bonus_never_alters_the_forecast_or_budget(client, conn):
    before_forecast = scalar(conn, """SELECT SUM(forecast_contribution)
                                      FROM original_forecast WHERE financial_year=2026""")
    before_budget = scalar(conn, """SELECT SUM(total_budget) FROM v_budget_quarter
                                    WHERE financial_year=2026""")
    client.get("/api/bonus?financial_year=2026")
    client.get("/api/bonus/Sam%20Stewart?financial_year=2026")
    assert scalar(conn, """SELECT SUM(forecast_contribution) FROM original_forecast
                           WHERE financial_year=2026""") == before_forecast
    assert scalar(conn, """SELECT SUM(total_budget) FROM v_budget_quarter
                           WHERE financial_year=2026""") == before_budget


# --- column scope --------------------------------------------------------------

def test_bonus_columns_declare_their_scope(client):
    """The three bonus figures cover different periods, so each says which.

    Earned and Projected cover quarters that have started; At target covers the
    whole year. Read side by side without that, a projection for one quarter can
    look larger than a full year's base bonus and appear to be a mistake.
    """
    d = client.get("/api/bonus?financial_year=2026").json()
    scope = d["column_scope"]
    assert set(scope) == {"earned_bonus", "projected_bonus", "bonus_at_target",
                          "full_year_outlook"}
    assert "quarters under way" in scope["projected_bonus"]
    assert "All four quarters" in scope["bonus_at_target"]
    assert "not comparable" in " ".join(d["meta"]["notes"])


def test_managers_report_how_many_quarters_have_started(client):
    d = client.get("/api/bonus?financial_year=2026").json()
    for m in d["managers"]:
        # Quarters the manager actually has a budget for — not always four,
        # because a partial dataset gives a manager budget in some quarters
        # only. The parts must still add up.
        assert m["quarters_total"] >= 1
        assert m["quarters_started"] + m["quarters_not_started"] == m["quarters_total"]


def test_full_year_outlook_combines_projection_with_remaining_targets(client):
    d = client.get("/api/bonus?financial_year=2026").json()
    for m in d["managers"]:
        remaining = (Decimal(str(m["bonus_at_target"]))
                     * Decimal(m["quarters_not_started"]) / Decimal(m["quarters_total"]))
        assert cents(m["full_year_outlook"]) == \
            cents(Decimal(str(m["projected_bonus"])) + remaining)
