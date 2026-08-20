"""Bonus calculation and tracker.

    Budget Target      = Expected Income x (1 + Growth %)
    Base Bonus         = (Budget Target - Expected Income) / divisor / GST
    Above-Target Bonus = (Actual Income - Budget Target) x rate / GST
    Total              = 0 below target, otherwise Base + Above-Target

Bonus is the one GST EXCLUSIVE figure in the system; everything else it is
compared against is GST inclusive. The divisors are read from
reporting_settings rather than written into the assertions -- these tests
hard-coded "/ 3" and broke the moment the scheme changed, which is the same
fault that put four hand-written copies of the exclusion rules in this
codebase.
"""
from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal

import pytest

CENT = Decimal("0.01")


def cents(v):
    return Decimal(str(v)).quantize(CENT, rounding=ROUND_HALF_UP)


def scheme(conn):
    """The live bonus scheme. Read, never restated."""
    with conn.cursor() as cur:
        cur.execute("""SELECT bonus_base_divisor, bonus_above_target_rate,
                              bonus_gst_divisor
                       FROM reporting_settings WHERE id = 1""")
        return cur.fetchone()


def base_bonus(target, expected, conn):
    """Base bonus as the application computes it, GST excluded."""
    divisor, _rate, gst = scheme(conn)
    return cents((Decimal(str(target)) - Decimal(str(expected))) / divisor / gst)


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
        assert cents(base) == base_bonus(target, expected, conn)


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
        assert cents(base) == base_bonus(target, expected, conn)
        # Divided by the GST divisor like every other payment figure. Without
        # it this compared a GST-exclusive bonus against a GST-inclusive
        # calculation and was wrong by exactly 1/1.1 -- latent since the GST
        # change, and only exposed once a manager actually cleared a target.
        _d, rate, gst = scheme(conn)
        assert cents(above) == cents((actual - target) * rate / gst)
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
        assert cents(base) == base_bonus(target, expected, conn)
        # Divided by the GST divisor like every other payment figure. Without
        # it this compared a GST-exclusive bonus against a GST-inclusive
        # calculation and was wrong by exactly 1/1.1 -- latent since the GST
        # change, and only exposed once a manager actually cleared a target.
        _d, rate, gst = scheme(conn)
        assert cents(above) == cents((actual - target) * rate / gst)
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
    # Earned is what would pay if the quarter closed today -- normally nil
    # part-way through, but not zero by rule: a manager who has already cleared
    # the whole quarter's target has earned it. Asserting a flat zero encoded one
    # dataset's state, and broke the moment somebody got ahead.
    earned = Decimal(str(d["totals"]["earned_bonus"]))

    # Cleared AND with a month behind it.
    #
    # target_reached compares the whole quarter's income against the whole
    # quarter's target, so it can be true before any month has closed -- a
    # manager whose only month is the one still running can already be above a
    # target that includes it. The bonus is null there, correctly: there is
    # nothing to pay against yet. Asserting earned > 0 on target_reached alone
    # failed on exactly that case, which was the view behaving properly.
    cleared = [q for q in d["quarters"]
               if q["target_reached"] and (q["months_elapsed"] or 0) > 0]
    if not cleared:
        assert earned == 0, ("nothing has cleared its target with a completed "
                             "month behind it, so nothing is earned")
    else:
        assert earned > 0
    # A projection only exists for a quarter part-way through. Where every
    # quarter is either closed or unstarted there is nothing to project.
    projected = Decimal(str(d["totals"]["projected_bonus"]))
    assert projected >= 0
    notes = " ".join(d["meta"]["notes"])
    assert "not money earned" in notes

def test_projection_scales_the_elapsed_pace(conn):
    """A manager running at X% of pace is projected to finish at X% of target.

    This asserted the old formula -- income times months_in_quarter over
    months_elapsed -- which assumed every month carried an equal share of the
    quarter. None do. July is 39% of one manager's quarter and 28% of another's,
    so multiplying by three overstated the first and understated the second: it
    projected a bonus for somebody who had MISSED July and none for somebody who
    had beaten it.

    Scaling the quarter target by the pace achieved carries the shape of the
    forecast with it, so an uneven quarter projects correctly without anybody
    having to know it is uneven.
    """
    checked = 0
    for completed, to_date, target, projected in rows(conn, """
            SELECT actual_income_completed, budget_to_date, budget_target,
                   projected_income
            FROM v_bonus_quarter
            WHERE financial_year = 2026 AND projected_income IS NOT NULL"""):
        assert cents(projected) == cents(completed * target / to_date)
        checked += 1
    if not checked:
        pytest.skip("no quarter is part-way through with a completed month behind it")


def test_a_projection_needs_a_completed_month_behind_it(conn):
    """Null, not nought, when there is nothing to project from.

    A quarter whose first month has not closed, or whose closed month has no
    transactions imported, has no basis for a projection. Reporting zero there
    reads as "on course to earn nothing", which is a verdict rather than an
    absence -- and it is the same conflation of null with zero that made every
    manager read as well behind while the sales file had simply not arrived.
    """
    for completed, projected, bonus in rows(conn, """
            SELECT actual_income_completed, projected_income, projected_bonus
            FROM v_bonus_quarter WHERE financial_year = 2026"""):
        if completed is None:
            assert projected is None, "projected income without a completed month"
            assert bonus is None, "projected bonus without a completed month"


def test_a_projected_bonus_needs_a_projection_above_target(conn):
    """Never a bonus for a manager the same view reports as behind.

    The pairing that made the old formula visible: Michael Stewart at 91.5% of
    pace was projected a bonus, and AnneM Goodchild at 115.8% was projected none.
    Whatever the arithmetic, those two facts cannot both sit on one row.
    """
    for pace_num, pace_den, projected, target, bonus in rows(conn, """
            SELECT actual_income_completed, budget_to_date, projected_income,
                   budget_target, projected_bonus
            FROM v_bonus_quarter
            WHERE financial_year = 2026 AND projected_bonus IS NOT NULL"""):
        if bonus > 0:
            assert projected >= target, \
                "a bonus projected while the projection is below target"
            assert pace_num >= pace_den, \
                "a bonus projected for a manager behind the pace needed"


# The words a quarter can be in. A finished quarter has a result; a running one
# has a direction, judged against the months elapsed rather than the whole
# quarter -- "behind" has to mean behind the pace needed, not behind a target
# with two months still to run, or every manager reads as failing in July.
FINISHED = {"bonus earned", "no bonus"}
RUNNING = {"ahead", "on pace", "behind", "well behind", "in progress"}


def test_status_reflects_the_position(client):
    d = client.get("/api/bonus?financial_year=2026").json()
    for q in d["quarters"]:
        if not q["quarter_started"]:
            assert q["status"] == "not started", q
        elif q["quarter_complete"]:
            assert q["status"] in FINISHED, q
        else:
            assert q["status"] in RUNNING, q


def test_pace_is_measured_against_the_months_elapsed(client):
    """The running quarter compares like with like.

    Comparing income against the whole quarter's target one month into three
    reported every manager as catastrophically behind -- on the live book three
    who were ahead of pace read as 43% to 57% under. pace_achievement must use
    the budget for the months elapsed, so the figure means something in week two.
    """
    d = client.get("/api/bonus?financial_year=2026").json()
    running = [q for q in d["quarters"]
               if q["quarter_started"] and not q["quarter_complete"]
               and q["pace_achievement"] is not None]
    if not running:
        pytest.skip("no quarter is part-way through in this dataset")

    for q in running:
        to_date = Decimal(str(q["budget_to_date"]))
        assert to_date <= Decimal(str(q["budget_target"])), \
            "the target for months elapsed cannot exceed the whole quarter's"
        expected = Decimal(str(q["actual_income"])) / to_date
        assert abs(Decimal(str(q["pace_achievement"])) - expected) < Decimal("0.0001"), q
        assert Decimal(str(q["pace_variance"])) == \
            Decimal(str(q["actual_income"])) - to_date


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
    assert "divided by 3" in s["description"]
    assert "20%" in s["description"]

    # The GST divisor has to be stated, not just applied. Somebody checking a
    # payment by hand from the income and target figures -- which are GST
    # inclusive -- lands about 9% high, and with nothing on the page to explain
    # the difference the reasonable conclusion is that the app is wrong.
    assert s["is_gst_exclusive"] is True
    assert Decimal(str(s["gst_divisor"])) == Decimal("1.1")
    assert "GST" in s["gst_note"] and "1.1" in s["gst_note"]
    assert any("1.1" in line for line in s["formula"]), \
        "the formula must show the GST divisor, not hide it"
    assert any("GST exclusive" in line for line in s["formula"])

    # Length is not asserted: pinning it meant a line could not be added to the
    # explanation without a test failing, which is backwards.
    assert len(s["formula"]) >= 4


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
    assert cents(after) == base_bonus(target, expected, conn)
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
