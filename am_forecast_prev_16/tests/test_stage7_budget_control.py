"""Month-level budget control, locking, and the forecast timeline."""
from __future__ import annotations

import datetime as dt
import os
from decimal import ROUND_HALF_UP, Decimal

import pytest

from conftest import budget_months, budgeted_manager

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
        cur.execute("DELETE FROM growth_rate WHERE created_by='pytest-admin'")
        cur.execute("DELETE FROM budget_lock WHERE locked_by='pytest-admin'")
        cur.execute("DELETE FROM budget_audit WHERE performed_by='pytest-admin'")
    conn.commit()


def month_row(client, manager, month, fy=2026):
    rows = client.get(f"/api/budget?financial_year={fy}").json()["monthly"]
    return next(r for r in rows
                if r["canonical_manager"] == manager and r["forecast_month"] == month)


# --- month-level growth --------------------------------------------------------

def test_growth_can_be_set_for_one_month_only(admin, conn):
    """A month-level rate moves that month and no other."""
    months = budget_months(conn)
    manager = budgeted_manager(conn)
    target = months[0].isoformat()
    others = [m.isoformat() for m in months[1:]]

    before_others = {m: month_row(admin, manager, m)["total_budget"] for m in others}
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager_month", "canonical_manager": manager,
        "target_month": target, "growth_pct": 0.15, "reason": "one month only"})
    assert res.status_code == 200

    r = month_row(admin, manager, target)
    assert r["growth_basis"] == "manager_month"
    assert cents(r["growth_pct"]) == Decimal("0.15")
    assert cents(r["new_business_growth_target"]) == \
        cents(Decimal(str(r["original_forecast"])) * Decimal("0.15"))

    for m, before in before_others.items():
        assert month_row(admin, manager, m)["total_budget"] == before, m

def test_month_rate_beats_manager_rate(admin, conn):
    """Resolution is most-specific-first: a month rate overrides a manager one."""
    months = budget_months(conn)
    manager = budgeted_manager(conn)
    target = months[0].isoformat()

    admin.post("/api/budget/growth-rate", json={
        "scope": "manager", "canonical_manager": manager,
        "financial_year": 2026, "growth_pct": 0.10, "reason": "manager level"})
    assert month_row(admin, manager, target)["growth_basis"] == "manager"

    admin.post("/api/budget/growth-rate", json={
        "scope": "manager_month", "canonical_manager": manager,
        "target_month": target, "growth_pct": 0.25, "reason": "month level"})
    assert month_row(admin, manager, target)["growth_basis"] == "manager_month"

    for m in months[1:]:
        assert month_row(admin, manager, m.isoformat())["growth_basis"] == "manager"

def test_quarter_rollup_reflects_month_level_rates(admin, conn):
    """A quarter rolls up from its months, so a month rate reaches it."""
    months = budget_months(conn)
    manager = budgeted_manager(conn)
    target = months[0]

    admin.post("/api/budget/growth-rate", json={
        "scope": "manager_month", "canonical_manager": manager,
        "target_month": target.isoformat(), "growth_pct": 0.50,
        "reason": "month rate"})

    with conn.cursor() as cur:
        cur.execute("""SELECT growth_basis, total_budget FROM v_budget_quarter
                       WHERE canonical_manager = %s
                         AND financial_year = au_financial_year(%s)
                         AND financial_quarter = au_quarter(%s)""",
                    (manager, target, target))
        basis, total = cur.fetchone()
        cur.execute("""SELECT SUM(total_budget) FROM v_monthly_budget
                       WHERE canonical_manager = %s
                         AND financial_year = au_financial_year(%s)
                         AND financial_quarter = au_quarter(%s)""",
                    (manager, target, target))
        month_total = cur.fetchone()[0]
    assert cents(total) == cents(month_total)
    # Where the quarter's months disagree, no single rate is claimed.
    assert basis in ("manager_month", "mixed")

def test_locking_freezes_a_month_against_later_changes(admin, conn):
    """A locked month keeps its figure even when the rate beneath it changes."""
    manager = budgeted_manager(conn)
    target = budget_months(conn)[0].isoformat()

    before = month_row(admin, manager, target)["total_budget"]
    lock = admin.post("/api/budget/lock", json={
        "canonical_manager": manager, "target_month": target,
        "reason": "agreed at review"})
    assert lock.status_code == 200
    assert cents(lock.json()["locked_budget"]) == cents(before)

    # Change the growth rate hard; the locked month must not move.
    admin.post("/api/budget/growth-rate", json={
        "scope": "manager_month", "canonical_manager": manager,
        "target_month": target, "growth_pct": 0.99, "reason": "must not apply"})
    after = month_row(admin, manager, target)
    assert after["is_locked"] is True
    assert cents(after["total_budget"]) == cents(before)


def test_unlocking_releases_the_month(admin, conn):
    manager = budgeted_manager(conn, rank=1)
    target = budget_months(conn)[0].isoformat()

    admin.post("/api/budget/lock", json={
        "canonical_manager": manager, "target_month": target,
        "reason": "lock then release"})
    admin.post("/api/budget/growth-rate", json={
        "scope": "manager_month", "canonical_manager": manager,
        "target_month": target, "growth_pct": 0.40, "reason": "queued change"})
    locked = month_row(admin, manager, target)["total_budget"]

    res = admin.post("/api/budget/unlock", json={
        "canonical_manager": manager, "target_month": target,
        "reason": "review reopened"})
    assert res.status_code == 200
    released = month_row(admin, manager, target)
    assert released["is_locked"] is False
    assert cents(released["total_budget"]) > cents(locked)

def test_locking_is_audited_with_reason_and_user(admin, conn):
    manager = budgeted_manager(conn, rank=2)
    target = budget_months(conn)[0].isoformat()
    admin.post("/api/budget/lock", json={
        "canonical_manager": manager, "target_month": target,
        "reason": "signed off at review"})
    with conn.cursor() as cur:
        cur.execute("""SELECT reason, locked_by, locked_renewal_forecast,
                              locked_growth_target, locked_budget
                       FROM budget_lock
                       WHERE canonical_manager = %s AND active""", (manager,))
        reason, by, forecast, growth, budget = cur.fetchone()
    assert reason == "signed off at review"
    assert by == "pytest-admin"
    # The components are stored too, so the lock stays explainable later.
    assert cents(forecast + growth) == cents(budget)

def test_double_lock_is_refused(admin, conn):
    body = {"canonical_manager": budgeted_manager(conn, rank=2),
            "target_month": budget_months(conn)[0].isoformat(),
            "reason": "first lock"}
    assert admin.post("/api/budget/lock", json=body).status_code == 200
    assert admin.post("/api/budget/lock", json=body).status_code == 409


def test_unlocking_an_unlocked_month_is_refused(admin, conn):
    assert admin.post("/api/budget/unlock", json={
        "canonical_manager": budgeted_manager(conn, rank=3),
        "target_month": budget_months(conn)[0].isoformat(),
        "reason": "not locked"}).status_code == 404


def test_viewer_cannot_lock_or_change_growth(client):
    client.headers.update({"X-Role": "viewer"})
    assert client.post("/api/budget/lock", json={
        "canonical_manager": "Retail", "target_month": "2027-05-01",
        "reason": "should fail"}).status_code == 403
    assert client.post("/api/budget/growth-rate", json={
        "scope": "manager_month", "canonical_manager": "Retail",
        "target_month": "2027-05-01", "growth_pct": 0.5,
        "reason": "should fail"}).status_code == 403


def test_locks_never_touch_the_forecast(admin, conn):
    before = scalar(conn, """SELECT SUM(forecast_contribution) FROM original_forecast
                             WHERE financial_year=2026""")
    admin.post("/api/budget/lock", json={
        "canonical_manager": "Sam Stewart", "target_month": "2027-01-01",
        "reason": "forecast must be untouched"})
    assert scalar(conn, """SELECT SUM(forecast_contribution) FROM original_forecast
                           WHERE financial_year=2026""") == before


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


# --- forecast history ----------------------------------------------------------

def test_forecast_history_lists_each_forecast_with_a_timestamp(client):
    d = client.get("/api/forecast-history?manager=Sam%20Stewart"
                   "&financial_year=2026").json()
    assert d["entry_count"] >= 1
    for e in d["entries"]:
        assert e["recorded_at"], e["label"]
        assert e["recorded_by"], e["label"]
        assert len(e["cells"]) == 12
    assert sum(1 for e in d["entries"] if e["is_current"]) == 1


def test_baselines_sort_before_snapshots(client):
    """A prior-year baseline covers months no snapshot reaches, so it is the
    starting position rather than a later revision."""
    d = client.get("/api/forecast-history?manager=Sam%20Stewart"
                   "&financial_year=2026").json()
    kinds = [e["kind"] for e in d["entries"]]
    if "snapshot" in kinds and any(k != "snapshot" for k in kinds):
        assert kinds.index("snapshot") > 0
        # The live forecast is the newest snapshot, not the baseline.
        current = next(e for e in d["entries"] if e["is_current"])
        assert current["kind"] == "snapshot"


def test_history_totals_match_the_snapshot(client, conn):
    d = client.get("/api/forecast-history?manager=Sam%20Stewart"
                   "&financial_year=2026").json()
    snapshot = next(e for e in d["entries"] if e["kind"] == "snapshot")
    assert cents(snapshot["total"]) == cents(scalar(conn, """
        SELECT SUM(p.forecast_contribution)
        FROM forecast_policy p
        LEFT JOIN v_manager_resolution r ON r.source_manager = p.source_manager
        WHERE NOT p.is_excluded AND p.financial_year = 2026
          AND COALESCE(r.canonical_manager, p.source_manager) = 'Sam Stewart'"""))


def test_history_rejects_an_unknown_manager(client):
    assert client.get("/api/forecast-history?manager=Nobody").status_code == 404


# --- scope guards --------------------------------------------------------------

def test_naming_a_manager_with_global_scope_is_refused(admin):
    """The dangerous reading of this payload is the silent one.

    A manager named alongside global scope used to be ignored, so a change
    intended for one person moved everybody's budget.
    """
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "global", "canonical_manager": "Liam Thornton",
        "growth_pct": 0.20, "reason": "contradictory scope"})
    assert res.status_code == 400
    assert "applies to every manager" in res.json()["detail"]


def test_manager_scope_requires_a_manager(admin):
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager", "canonical_manager": None,
        "growth_pct": 0.20, "reason": "no manager named"})
    assert res.status_code == 400
    assert "requires a manager" in res.json()["detail"]


def test_quarter_scope_requires_year_and_quarter(admin):
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager_quarter", "canonical_manager": "Sam Stewart",
        "growth_pct": 0.20, "reason": "missing quarter"})
    assert res.status_code == 400
    assert "financial_quarter" in res.json()["detail"]


def test_a_manager_change_moves_only_that_manager(admin, conn):
    """The report behind the original complaint, asserted end to end."""
    def budgets():
        return {r["canonical_manager"]: Decimal(str(r["total_budget"]))
                for r in admin.get("/api/budget?financial_year=2026").json()["quarters"]
                if r["financial_quarter"] == 1}

    before = budgets()
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager", "canonical_manager": "Liam Thornton",
        "financial_year": 2026, "growth_pct": 0.20,
        "reason": "single manager change"})
    assert res.status_code == 200
    after = budgets()
    assert after["Liam Thornton"] > before["Liam Thornton"]
    for name, value in before.items():
        if name != "Liam Thornton":
            assert after[name] == value, f"{name} moved and should not have"


def test_quarter_scope_moves_only_that_quarter(admin, conn):
    """A quarter-scoped rate moves that quarter and no other."""
    manager = budgeted_manager(conn)
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT financial_quarter FROM v_budget_quarter
                       WHERE canonical_manager = %s AND financial_year = 2026
                       ORDER BY 1""", (manager,))
        quarters = [r[0] for r in cur.fetchall()]
    if not quarters:
        pytest.skip("no budget quarters for this manager")

    def q_budget(q):
        return next((Decimal(str(r["total_budget"]))
                     for r in admin.get("/api/budget?financial_year=2026").json()["quarters"]
                     if r["canonical_manager"] == manager
                     and r["financial_quarter"] == q), None)

    before = {q: q_budget(q) for q in quarters}
    target = quarters[0]
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager_quarter", "canonical_manager": manager,
        "financial_year": 2026, "financial_quarter": target, "growth_pct": 0.30,
        "reason": "one quarter only"})
    assert res.status_code == 200
    assert q_budget(target) > before[target]
    for q in quarters[1:]:
        assert q_budget(q) == before[q], f"Q{q} moved and should not have"
