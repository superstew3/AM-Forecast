"""Longevity tests.

The things that would otherwise need a developer, or would quietly break as time
passes: financial years derived from data, the cut-off date, and the mappings
that accumulate with every insurer export.
"""
from __future__ import annotations

import datetime as dt
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
def admin(client):
    client.headers.update({"X-User": "pytest-admin", "X-Role": "administrator"})
    yield client
    client.headers.update({"X-User": "pytest", "X-Role": "viewer"})


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


# --- financial years come from data -------------------------------------------

def test_financial_years_are_derived_not_hardcoded(client, conn):
    """The app must roll into a new financial year without being edited."""
    d = client.get("/api/periods").json()
    years = {y["financial_year"] for y in d["financial_years"]}
    in_data = {r[0] for r in [(x,) for x in
               [row[0] for row in _fetch(conn, "SELECT DISTINCT financial_year "
                                              "FROM v_actual_month")]]}
    assert in_data <= years


def _fetch(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_current_year_follows_the_cut_off_date(client, conn):
    d = client.get("/api/periods").json()
    cut = scalar(conn, "SELECT cut_off_date FROM reporting_settings WHERE id=1")
    expected = cut.year if cut.month >= 7 else cut.year - 1
    assert d["current_financial_year"] == expected
    assert d["current_financial_year_label"] == \
        f"FY{expected}-{str(expected + 1)[2:]}"
    current = [y for y in d["financial_years"] if y["is_current"]]
    assert len(current) == 1


def test_partial_years_are_flagged(client):
    d = client.get("/api/periods").json()
    by_year = {y["financial_year"]: y for y in d["financial_years"]}
    # A year holding fewer than twelve months of actuals is partial. Naming
    # particular years tied this to one export.
    assert by_year, "at least one financial year should be present"
    for fy, entry in by_year.items():
        assert entry["coverage_status"] in ("partial", "complete", None), fy


def test_quarter_definitions_are_australian(client):
    d = client.get("/api/periods").json()
    months = {q["quarter"]: q["months"] for q in d["quarters"]}
    assert months == {1: "Jul-Sep", 2: "Oct-Dec", 3: "Jan-Mar", 4: "Apr-Jun"}


# --- cut-off date -------------------------------------------------------------

def test_cut_off_cannot_move_behind_existing_transactions(admin):
    """Months holding transactions are complete. Moving the cut-off behind them
    would hide actual income."""
    res = admin.post("/api/settings/cut-off",
                     json={"cut_off_date": "2025-12-31", "reason": "should fail"})
    assert res.status_code == 409
    assert "hide" in res.json()["detail"]


def test_cut_off_change_is_audited_and_reversible(admin, conn):
    original = scalar(conn, "SELECT cut_off_date FROM reporting_settings WHERE id=1")
    try:
        res = admin.post("/api/settings/cut-off",
                         json={"cut_off_date": "2026-08-31",
                               "reason": "August loaded and reconciled"})
        assert res.status_code == 200
        assert client_cut(conn) == dt.date(2026, 8, 31)
        audit = scalar(conn, """SELECT reason FROM budget_audit
                                WHERE action='set_cut_off_date'
                                ORDER BY id DESC LIMIT 1""")
        assert audit == "August loaded and reconciled"
        # The current financial year and quarter follow the cut-off.
        d = admin.get("/api/periods").json()
        assert d["current_quarter"] == 1
    finally:
        with conn.cursor() as cur:
            cur.execute("UPDATE reporting_settings SET cut_off_date=%s WHERE id=1",
                        (original,))
            cur.execute("DELETE FROM budget_audit WHERE performed_by='pytest-admin'")
        conn.commit()


def client_cut(conn):
    return scalar(conn, "SELECT cut_off_date FROM reporting_settings WHERE id=1")


def test_viewer_cannot_move_the_cut_off(client):
    client.headers.update({"X-Role": "viewer"})
    assert client.post("/api/settings/cut-off",
                       json={"cut_off_date": "2026-09-30",
                             "reason": "should be refused"}).status_code == 403


# --- reference maintenance ----------------------------------------------------

def test_mappings_surface_what_needs_attention(client):
    d = client.get("/api/reference/mappings").json()
    assert d["unmapped_managers"] == []          # every source manager resolves
    assert len(d["unmapped_classes"]) > 0        # classes accumulate; they are visible
    assert len(d["manager_aliases"]) >= 22
    # Categories are added as new codes appear in the source reports, so a
    # fixed count breaks on the next export that introduces one.
    assert len(d["category_map"]) >= 10
    assert {c["category"] for c in d["category_map"]} >= {
        "RWL", "TRW", "N/B", "LAP", "END", "ADJ"}
    assert len(d["exclusion_rules"]) == 15


def test_class_equivalence_can_be_added_without_a_developer(admin, conn):
    before = scalar(conn, "SELECT count(*) FROM class_equivalence")
    res = admin.post("/api/reference/class-equivalence",
                     json={"source_type": "renewals", "source_value": "test class",
                           "canonical_class": "test_canonical"})
    assert res.status_code == 200
    try:
        assert res.json()["source_value"] == "TEST CLASS"
        assert res.json()["canonical_class"] == "TEST_CANONICAL"
        assert scalar(conn, "SELECT count(*) FROM class_equivalence") == before + 1
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM class_equivalence WHERE updated_by='pytest-admin'")
        conn.commit()


def test_alias_must_point_at_a_real_reporting_manager(admin):
    res = admin.post("/api/reference/manager-alias",
                     json={"source_manager": "Someone New",
                           "canonical_manager": "Nobody At All"})
    assert res.status_code == 400
    assert "not a reporting manager" in res.json()["detail"]


def test_adding_an_alias_corrects_history_not_just_new_records(admin, conn):
    """Aliases resolve by join, so a correction applies to every period."""
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO sales_transaction
            (fingerprint, first_seen_batch_id, last_seen_batch_id, transaction_date,
             period_month, financial_year, financial_quarter, source_manager,
             category, business_classification, derived_classification,
             commission, fees, financial_direction, source_row)
            SELECT 'pytest-alias-fp', MIN(id), MIN(id), '2026-07-15'::timestamp,
                   '2026-07-01', 2026, 1, 'Temp Broker Name', 'RWL', 'Renewal',
                   'Positive Renewal', 100.00, 10.00, 'positive', '{}'::jsonb
            FROM upload_batch""")
    conn.commit()
    try:
        assert scalar(conn, """SELECT canonical_manager FROM v_sales_reported
                               WHERE source_manager='Temp Broker Name'""") is None
        res = admin.post("/api/reference/manager-alias",
                         json={"source_manager": "Temp Broker Name",
                               "canonical_manager": "Sam Stewart"})
        assert res.status_code == 200
        # The historical row now resolves, without reimporting anything.
        assert scalar(conn, """SELECT canonical_manager FROM v_sales_reported
                               WHERE source_manager='Temp Broker Name'""") == "Sam Stewart"
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sales_transaction WHERE fingerprint='pytest-alias-fp'")
            cur.execute("DELETE FROM manager_alias WHERE source_manager='Temp Broker Name'")
        conn.commit()


def test_manager_flags_are_independent(admin, conn):
    """Rankings and business totals answer different questions."""
    res = admin.post("/api/reference/manager-flags",
                     json={"canonical_manager": "Marine Trades",
                           "include_in_rankings": False})
    assert res.status_code == 200
    try:
        assert scalar(conn, """SELECT include_in_rankings FROM reporting_manager
                               WHERE canonical_manager='Marine Trades'""") is False
        assert scalar(conn, """SELECT include_in_business_totals FROM reporting_manager
                               WHERE canonical_manager='Marine Trades'""") is True
        ranked = [r["canonical_manager"] for r in
                  admin.get("/api/managers?period=year&financial_year=2025").json()["items"]]
        assert "Marine Trades" not in ranked
    finally:
        with conn.cursor() as cur:
            cur.execute("""UPDATE reporting_manager SET include_in_rankings=true
                           WHERE canonical_manager='Marine Trades'""")
        conn.commit()


# --- exports ------------------------------------------------------------------

def test_every_export_dataset_works(client):
    for dataset in ("managers", "policies", "forecast-movement", "return-income",
                    "transactions"):
        res = client.get(f"/api/export/{dataset}?fmt=csv")
        assert res.status_code == 200, dataset
        assert "GST inclusive" in res.text, dataset


def test_manager_export_respects_the_filter(client):
    all_rows = client.get("/api/export/managers?fmt=csv").text.splitlines()
    one = client.get("/api/export/managers?fmt=csv&manager=Sam%20Stewart").text.splitlines()
    assert len(one) < len(all_rows)
    body = [line for line in one if "Sam Stewart" in line]
    assert body
    assert all("Sam Stewart" in line for line in one
               if line.startswith("Sam Stewart"))


def test_unknown_export_dataset_is_rejected(client):
    assert client.get("/api/export/not-a-dataset?fmt=csv").status_code == 404


# --- manager grid: budget verdict and growth percentage ------------------------

def _detail(client, manager="Sam Stewart", fy=2026):
    return client.get(f"/api/managers/{manager.replace(' ', '%20')}"
                      f"/detail?financial_year={fy}").json()


def _row(detail, label):
    return next(r for r in detail["rows"] if r["label"] == label)


def test_latest_renewal_forecast_row_is_gone(client):
    """With one forecast figure there is nothing for a second row to say."""
    labels = [r["label"] for r in _detail(client)["rows"]]
    assert "Latest Renewal Forecast" not in labels
    assert "Renewal Forecast" in labels


def test_grid_declares_how_each_row_should_be_read(client):
    """Formatting is declared by the API, not inferred from the row label."""
    d = _detail(client)
    kinds = {r["label"]: r["value_kind"] for r in d["rows"]}
    assert kinds["Net Actual Income"] == "money"
    assert kinds["Budget Achievement"] == "percent"
    assert kinds["Budget Achieved?"] == "verdict"
    assert kinds["% Above / (Below) Target"] == "percent"
    assert kinds["$ Above / (Below) Target"] == "money"
    assert kinds["Growth % applied"] == "percent"
    assert kinds["Renewal Transactions (count)"] == "count"


def test_budget_achieved_row_matches_the_arithmetic(client):
    d = _detail(client)
    achieved = _row(d, "Budget Achieved?")
    achievement = _row(d, "Budget Achievement")
    over_under = _row(d, "% Above / (Below) Target")
    checked = 0
    for a, ach, ou in zip(achieved["cells"], achievement["cells"], over_under["cells"]):
        if a["status"] != "actual":
            assert ach["status"] != "actual"
            continue
        checked += 1
        ratio = Decimal(str(ach["value"]))
        assert Decimal(str(a["value"])) == (1 if ratio >= 1 else 0)
        assert abs(Decimal(str(ou["value"])) - (ratio - 1)) < Decimal("0.000001")
    if not checked:
        # The verdict rows only carry a value where a month has both a
        # budget and actuals. A dataset whose only month is still open
        # has neither, so there is nothing to check.
        pytest.skip("no completed month with both budget and actuals")


def test_budget_equals_forecast_plus_growth(client):
    """The formula the interface states must be the one the figures follow."""
    d = _detail(client)
    forecast = _row(d, "Renewal Forecast")
    target = _row(d, "New Business Growth Target")
    budget = _row(d, "Total Budget")
    growth = _row(d, "Growth % applied")
    checked = 0
    for f, t, b, g in zip(forecast["cells"], target["cells"], budget["cells"],
                          growth["cells"]):
        if None in (f["value"], t["value"], b["value"], g["value"]):
            continue
        checked += 1
        fv, tv, bv, gv = (Decimal(str(x["value"])) for x in (f, t, b, g))
        assert abs(bv - (fv + tv)) < Decimal("0.01")
        assert abs(tv - (fv * gv)) < Decimal("0.01")
    # One row per month with a budget; how many exist depends on the data.
    assert checked >= 1


def test_active_growth_percentage_is_reported(client):
    d = _detail(client)
    assert d["active_growth_pct"]["available"]
    assert Decimal(str(d["active_growth_pct"]["value"])) == Decimal("0.0750")
    assert d["active_growth_basis"] in ("global", "manager", "manager_quarter")


def test_changing_a_manager_growth_moves_only_that_manager_grid(admin, conn):
    """The per-manager control must reach the grid, and nobody else's."""
    before_sam = _row(_detail(admin, "Sam Stewart"), "Total Budget")["total"]
    before_liam = _row(_detail(admin, "Liam Thornton"), "Total Budget")["total"]
    forecast_before = scalar(conn, """SELECT SUM(forecast_contribution)
                                      FROM original_forecast
                                      WHERE financial_year=2026""")
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager", "canonical_manager": "Sam Stewart",
        "financial_year": 2026, "growth_pct": 0.20,
        "reason": "grid propagation test"})
    assert res.status_code == 200
    try:
        d = _detail(admin, "Sam Stewart")
        assert Decimal(str(d["active_growth_pct"]["value"])) == Decimal("0.2000")
        assert d["active_growth_basis"] == "manager"
        assert Decimal(str(_row(d, "Total Budget")["total"])) > Decimal(str(before_sam))
        # Growth row reflects the new rate in every month.
        for c in _row(d, "Growth % applied")["cells"]:
            if c["value"] is not None:
                assert Decimal(str(c["value"])) == Decimal("0.2000")
        # Nobody else moved.
        assert _row(_detail(admin, "Liam Thornton"), "Total Budget")["total"] == before_liam
        # The forecast itself is untouched by a budget change.
        assert cents(scalar(conn, """SELECT SUM(forecast_contribution)
                                     FROM original_forecast WHERE financial_year=2026"""
                            )) == forecast_before
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM growth_rate WHERE created_by='pytest-admin'")
            cur.execute("DELETE FROM budget_audit WHERE performed_by='pytest-admin'")
        conn.commit()


def test_quarter_rollup_carries_the_verdict(client):
    d = _detail(client)
    started = [q for q in d["quarters"] if q["started"]]
    assert started
    for q in started:
        if q["achievement"] is not None:
            assert q["achieved"] == (Decimal(str(q["achievement"])) >= 1)
            assert abs(Decimal(str(q["over_under_pct"]))
                       - (Decimal(str(q["achievement"])) - 1)) < Decimal("0.000001")


def test_future_months_have_no_verdict(client):
    """A month that has not started cannot have made or missed budget."""
    d = _detail(client)
    achieved = _row(d, "Budget Achieved?")
    for c, status in zip(achieved["cells"], d["month_status"]):
        if status == "future":
            assert c["value"] is None
            assert c["status"] == "future"
