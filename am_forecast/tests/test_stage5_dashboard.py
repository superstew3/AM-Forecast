"""Dashboard acceptance tests.

The twenty checks required for the dashboard, run against the live API and the
database views behind it. Numbered to match the specification.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
from decimal import ROUND_HALF_UP, Decimal

import pytest

CENT = Decimal("0.01")


def cents(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


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


# --- 1. base operating position ----------------------------------------------

def test_01_base_operating_position_reconciles(client):
    d = client.get("/api/base-position").json()
    assert d["checks"]["original_renewal_forecast"]
    assert d["checks"]["total_budget"]
    assert d["checks"]["latest_outlook"]
    assert d["checks"]["remaining_budget_gap"]
    assert d["checks"]["cut_off_date"]
    assert d["is_base_state"], d["checks"]
    assert d["live"]["rounded"] == {
        "original_renewal_forecast": "3677092.30",
        "total_budget": "3952874.22",
        "latest_outlook": "3676619.01",
        "remaining_budget_gap": "276255.21",
    }


# --- 2. no synthetic data -----------------------------------------------------

def test_02_no_synthetic_data_present(client, conn):
    d = client.get("/api/base-position").json()
    assert d["live"]["snapshots"] == 1
    assert d["live"]["transactions"] == 14886
    # Fixture invoice numbers start at 8,800,000.
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE invoice_number >= 8800000""") == 0


# --- 3. dashboard totals equal view totals ------------------------------------

def test_03_dashboard_totals_equal_view_totals(client, conn):
    d = client.get("/api/business?financial_year=2026").json()
    for field, sql in [
        ("net_actual_income",
         "SELECT SUM(net_actual_income) FROM v_actual_month WHERE financial_year=2026"),
        ("original_renewal_forecast",
         """SELECT SUM(original_forecast) FROM v_forecast_position_month
            WHERE financial_year=2026"""),
        ("total_budget",
         "SELECT SUM(total_budget) FROM v_budget_quarter WHERE financial_year=2026"),
        ("latest_outlook",
         "SELECT SUM(latest_outlook) FROM v_outlook_quarter WHERE financial_year=2026"),
    ]:
        assert cents(d[field]["value"]) == cents(scalar(conn, sql)), field


# --- 4. positive + signed return = net ----------------------------------------

def test_04_positive_plus_return_equals_net(client, conn):
    d = client.get("/api/business?financial_year=2026").json()
    positive = Decimal(str(d["positive_actual_income"]["value"]))
    absolute_return = Decimal(str(d["return_income"]["value"]))
    net = Decimal(str(d["net_actual_income"]["value"]))
    # The dashboard shows the absolute return; the identity uses the signed one.
    assert cents(positive - absolute_return) == cents(net)
    signed = scalar(conn, """SELECT SUM(signed_return_income) FROM v_actual_month
                             WHERE financial_year=2026""")
    assert cents(positive + Decimal(str(signed))) == cents(net)


# --- 5. N/A is not zero -------------------------------------------------------

def test_05_unavailable_measures_are_null_not_zero(client):
    rows = client.get("/api/managers?period=month&financial_year=2026"
                      "&include_non_ranked=true").json()["items"]
    july = [r for r in rows if r["period_month"] == "2026-07-01"]
    assert july
    unavailable = [r for r in july if not r["renewal_achievement"]["available"]]
    assert unavailable, "expected at least one manager with no July baseline"
    for r in unavailable:
        assert r["renewal_achievement"]["value"] is None
        assert r["renewal_achievement"]["value"] != 0
        assert r["renewal_achievement"]["reason"]


def test_05b_july_baseline_now_covers_every_manager(client):
    """The prior-year baseline exists for everyone who traded last July.

    Under the legacy dashboard baseline, Cameron Stewart, Dinghy Scheme and
    Anastasia K had no July figure and reported N/A. Prior-year actual removes
    that gap for anyone with prior-year income.
    """
    rows = client.get("/api/managers?period=month&financial_year=2026"
                      "&include_non_ranked=true").json()["items"]
    july = {r["canonical_manager"]: r for r in rows if r["period_month"] == "2026-07-01"}
    assert july
    measurable = [r for r in july.values() if r["budget_achievement"]["available"]]
    assert len(measurable) >= 12


# --- 6. completed months have no Latest Forecast ------------------------------

def test_06_completed_month_has_no_latest_forecast(client, conn):
    assert scalar(conn, """SELECT SUM(latest_forecast) FROM v_forecast_position_month
                           WHERE forecast_month = DATE '2026-07-01'""") is None
    rows = client.get("/api/managers?period=month&financial_year=2026"
                      "&include_non_ranked=true").json()["items"]
    july = [r for r in rows if r["period_month"] == "2026-07-01"]
    assert july
    for r in july:
        assert not r["latest_forecast"]["available"]
        assert "completed" in (r["latest_forecast"]["reason"] or "").lower()


# --- 7. July legacy baseline warning ------------------------------------------

def test_07_july_baseline_is_declared(client, conn):
    notes = " ".join(client.get("/api/business?financial_year=2026").json()["meta"]["notes"])
    assert "supplied per-manager forecast" in notes
    assert "August 2026" in notes
    baseline = client.get("/api/data-quality").json()["baselines"]
    july = next(b for b in baseline if b["forecast_month"] == "2026-07-01")
    assert "Supplied figures" in july["baseline_source"]
    # Prior-year actual exists for every manager, so no exceptions remain.
    assert july["manager_exceptions"] == []
    # July must not claim policy-level original detail.
    assert scalar(conn, """SELECT count(*) FROM original_forecast
                           WHERE forecast_month = DATE '2026-07-01'
                             AND grain = 'policy'""") == 0


# --- 8. twelve zero-income pending policies -----------------------------------

def test_08_twelve_zero_income_policies_in_data_quality(client):
    d = client.get("/api/data-quality").json()
    assert d["counts"]["zero_expected_policies"] == 12
    assert d["expected"]["zero_expected_policies"] == 12
    assert "12" in d["notes"]["zero_expected_policies"]
    detail = client.get("/api/data-quality/zero_expected_policies").json()
    assert detail["total"] == 12
    assert all(Decimal(str(r["raw_expected_income"])) == 0 for r in detail["items"])
    assert any(r["policy_id"] == 931173620 for r in detail["items"])


# --- 9 and 10. Highview exclusion ---------------------------------------------

def test_09_highview_absent_from_reported_totals(client, conn):
    d = client.get("/api/business?financial_year=2026").json()
    reported = cents(d["net_actual_income"]["value"])
    including = cents(scalar(conn, """
        SELECT SUM(actual_income) FROM sales_transaction
        WHERE financial_year = 2026"""))
    assert reported != including
    assert reported == cents(scalar(conn, """
        SELECT SUM(actual_income) FROM sales_transaction
        WHERE financial_year = 2026 AND NOT is_excluded"""))


def test_10_highview_remains_in_the_excluded_audit_view(client):
    d = client.get("/api/data-quality").json()
    assert d["counts"]["excluded_sales_records"] == 2163
    assert d["counts"]["excluded_forecast_records"] == 975
    detail = client.get("/api/data-quality/excluded_records").json()
    assert detail["total"] == 2163 + 975
    assert all(r["exclusion_field"] for r in detail["items"])


# --- 11. Anastasia K ----------------------------------------------------------

def test_11_anastasia_in_totals_but_not_rankings(client, conn):
    """Included in business totals, out of rankings, achievement N/A.

    Tested against FY2025-26, which is where her income actually falls. She has
    no FY2026-27 activity at all, so that year would prove nothing.
    """
    fy = 2025
    ranked = [r["canonical_manager"] for r in
              client.get(f"/api/managers?period=year&financial_year={fy}").json()["items"]]
    assert "Anastasia K" not in ranked

    everyone = [r["canonical_manager"] for r in
                client.get(f"/api/managers?period=year&financial_year={fy}"
                           "&include_non_ranked=true").json()["items"]]
    assert "Anastasia K" in everyone

    assert scalar(conn, """SELECT include_in_business_totals FROM reporting_manager
                           WHERE canonical_manager='Anastasia K'""") is True
    assert scalar(conn, """SELECT include_in_rankings FROM reporting_manager
                           WHERE canonical_manager='Anastasia K'""") is False

    # Her income is inside the business total, which is what "included in
    # business totals" has to mean to be worth anything.
    her_income = scalar(conn, """SELECT SUM(net_actual_income) FROM v_actual_month
                                 WHERE canonical_manager='Anastasia K'
                                   AND financial_year=%s""", (fy,))
    assert her_income > 0
    business = Decimal(str(client.get(f"/api/business?financial_year={fy}")
                           .json()["net_actual_income"]["value"]))
    without_her = scalar(conn, """SELECT SUM(net_actual_income) FROM v_actual_month
                                  WHERE canonical_manager <> 'Anastasia K'
                                    AND financial_year=%s""", (fy,))
    assert cents(business) == cents(Decimal(str(without_her)) + Decimal(str(her_income)))

    # No pending book, so no budget and achievement is N/A rather than 0%.
    her = next(r for r in client.get(f"/api/managers?period=year&financial_year={fy}"
                                     "&include_non_ranked=true").json()["items"]
               if r["canonical_manager"] == "Anastasia K")
    assert not her["total_budget"]["available"]
    assert not her["budget_achievement"]["available"]
    assert her["budget_achievement"]["value"] is None


# --- 12. manager transfers by flag --------------------------------------------

def test_12_manager_transfers_counted_by_independent_flag(client, conn):
    totals = client.get("/api/forecast-movement").json()["totals"]
    by_flag = scalar(conn, "SELECT count(*) FROM forecast_movement WHERE manager_changed")
    assert totals["manager_transfers"] == by_flag
    by_primary = scalar(conn, """SELECT count(*) FROM forecast_movement
                                 WHERE movement_type='manager_changed'""")
    assert by_flag >= by_primary


# --- 13. no double credit -----------------------------------------------------

def test_13_no_transaction_credited_to_several_policies(client, conn):
    assert scalar(conn, "SELECT count(*) FROM v_allocation_breaches") == 0
    assert client.get("/api/data-quality").json()["counts"]["allocation_breaches"] == 0
    assert scalar(conn, """SELECT COALESCE(MAX(c),0) FROM (
        SELECT count(*) AS c FROM match_allocation WHERE method='auto'
        GROUP BY transaction_id) x""") <= 1


# --- 14 and 15. renewal income composition ------------------------------------

def test_14_renewal_income_is_rwl_trw_plus_linked_corrections(conn):
    offenders = scalar(conn, """
        SELECT count(*) FROM match_allocation a
        JOIN sales_transaction t ON t.id = a.transaction_id
        WHERE a.is_renewal_income
          AND t.category NOT IN ('RWL','TRW')
          AND a.allocation_basis NOT LIKE '%%invoice chain%%'
          AND a.method <> 'manual'""")
    assert offenders == 0


def test_15_lapse_reduces_net_but_yields_zero_renewal_income(client, conn):
    assert scalar(conn, """SELECT count(*) FROM policy_outcome
                           WHERE outcome='lapsed_lost'
                             AND renewal_transaction_income <> 0""") == 0
    d = client.get("/api/business?financial_year=2026").json()
    lapse = Decimal(str(d["lapse_return_income"]["value"]))
    assert lapse >= 0
    returns = client.get("/api/return-income").json()["items"]
    lapse_row = [r for r in returns if r["derived_classification"] == "Lapse / Lost Renewal"]
    if lapse_row:
        assert Decimal(str(lapse_row[0]["signed_return_income"])) < 0


# --- 16 and 17. budget stability and scope ------------------------------------

def test_16_changing_latest_forecast_does_not_change_budget(conn):
    """Budget derives from the frozen forecast alone.

    Asserted across the whole view chain rather than one definition, since the
    quarterly view now rolls up from the monthly one.
    """
    chain = " ".join(
        scalar(conn, "SELECT pg_get_viewdef(%s, true)", (view,))
        for view in ("v_budget_quarter", "v_monthly_budget"))
    assert "v_original_forecast_month" in chain
    assert "v_latest_forecast" not in chain
    assert "forecast_policy" not in chain


def test_17_budget_override_affects_only_its_scope(admin, conn):
    before = {r["canonical_manager"]: r["total_budget"] for r in
              admin.get("/api/budget?financial_year=2026").json()["quarters"]
              if r["financial_quarter"] == 1}
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager_quarter", "canonical_manager": "Sam Stewart",
        "financial_year": 2026, "financial_quarter": 1, "growth_pct": 0.2,
        "reason": "acceptance test"})
    assert res.status_code == 200
    try:
        after = {r["canonical_manager"]: r["total_budget"] for r in
                 admin.get("/api/budget?financial_year=2026").json()["quarters"]
                 if r["financial_quarter"] == 1}
        assert after["Sam Stewart"] > before["Sam Stewart"]
        for name, value in before.items():
            if name != "Sam Stewart":
                assert after[name] == value, name
        audit = admin.get("/api/budget/audit").json()["items"][0]
        assert audit["reason"] == "acceptance test"
        assert audit["performed_by"] == "pytest-admin"
    finally:
        with conn.cursor() as cur:
            cur.execute("""DELETE FROM growth_rate
                           WHERE scope='manager_quarter' AND created_by='pytest-admin'""")
            cur.execute("DELETE FROM budget_audit WHERE performed_by='pytest-admin'")
        conn.commit()


# --- 18. summary reconciles to drill-down -------------------------------------

def test_18_summary_reconciles_to_drilldown(client, conn):
    summary = client.get("/api/return-income").json()
    total = cents(summary["total"]["absolute"])
    parts = sum(Decimal(str(r["absolute_return_income"])) for r in summary["items"])
    assert cents(parts) == total

    movement = client.get("/api/forecast-movement").json()
    per_month = sum(Decimal(str(r["expected_income_removed"])) for r in movement["summary"])
    assert cents(per_month) == cents(movement["totals"]["income_removed"])

    dq = client.get("/api/data-quality").json()["counts"]["zero_expected_policies"]
    assert client.get("/api/data-quality/zero_expected_policies").json()["total"] == dq


# --- 19. accept uses the previewed figures ------------------------------------

def test_19_accept_uses_the_exact_previewed_figures(admin, conn, tmp_path):
    import polars as pl
    src = "/mnt/user-data/uploads/Sales_Transaction_List_25-26.csv"
    sample = pl.read_csv(src, infer_schema_length=0).head(200).with_columns(
        (pl.col("InvNumber").cast(pl.Int64) + 6_600_000).cast(pl.Utf8).alias("InvNumber"))
    path = tmp_path / "preview_check.csv"
    sample.write_csv(path)

    with path.open("rb") as fh:
        preview = admin.post("/api/uploads/prepare",
                             files={"file": ("preview_check.csv", fh, "text/csv")}).json()
    batch_id = preview["batch_id"]
    try:
        before = scalar(conn, """SELECT SUM(actual_income) FROM sales_transaction
                                 WHERE NOT is_excluded""")
        res = admin.post(f"/api/uploads/{batch_id}/accept", json={})
        assert res.status_code == 200, res.text
        after = scalar(conn, """SELECT SUM(actual_income) FROM sales_transaction
                                WHERE NOT is_excluded""")
        assert cents(after - before) == cents(preview["net_income"])
        batch = next(b for b in admin.get("/api/uploads").json()["items"]
                     if b["id"] == batch_id)
        assert batch["accepted_row_count"] == preview["valid_rows"]
        assert cents(batch["net_income"]) == cents(preview["net_income"])
    finally:
        admin.post(f"/api/uploads/{batch_id}/rollback",
                   json={"reason": "acceptance test cleanup"})


# --- 20. exports reconcile to the dashboard -----------------------------------

def test_20_export_reconciles_to_the_filtered_dashboard(client):
    params = "manager=Sam%20Stewart"
    screen = client.get(f"/api/policies?limit=1000&{params}").json()
    csv_text = client.get(f"/api/export/policies?fmt=csv&{params}").text
    reader = list(csv.reader(io.StringIO(csv_text)))
    header_row = next(i for i, r in enumerate(reader) if r and r[0] == "policy_id")
    header, body = reader[header_row], reader[header_row + 1:]
    assert len(body) == screen["total"]

    idx = header.index("original_forecast_income")
    exported = sum(Decimal(r[idx]) for r in body if r[idx] not in ("", "N/A"))
    on_screen = sum(Decimal(str(r["original_forecast_income"]))
                    for r in screen["items"]
                    if r["original_forecast_income"] is not None)
    assert cents(exported) == cents(on_screen)

    preamble = "\n".join(",".join(r) for r in reader[:header_row])
    assert "GST inclusive" in preamble
    assert "cut-off" in preamble.lower()
    assert "N/A" in preamble  # the N/A-is-not-zero statement
    kinds = reader[header_row - 1]
    assert "Original Forecast" in kinds
    assert "Actual" in kinds


# --- permissions --------------------------------------------------------------

def test_permissions_viewer_cannot_mutate(client):
    client.headers.update({"X-User": "pytest", "X-Role": "viewer"})
    assert client.post("/api/budget/growth-rate", json={
        "scope": "global", "growth_pct": 0.5, "reason": "should fail"}).status_code == 403
    assert client.post("/api/review/rematch").status_code == 403
    assert client.get("/api/business?financial_year=2026").status_code == 200
    assert client.get("/api/export/policies?fmt=csv").status_code == 200


def test_permissions_are_declared_per_role(client):
    client.headers.update({"X-Role": "viewer"})
    viewer = client.get("/api/session").json()["can"]
    assert viewer["view"] and viewer["export"]
    assert not viewer["upload"] and not viewer["adjust_budget"]
    client.headers.update({"X-Role": "administrator"})
    admin_can = client.get("/api/session").json()["can"]
    assert all(admin_can.values())
    client.headers.update({"X-Role": "viewer"})


def test_every_financial_page_states_gst(client):
    for path in ["/api/business?financial_year=2026", "/api/managers", "/api/return-income",
                 "/api/new-business", "/api/policies?limit=1", "/api/forecast-movement",
                 "/api/budget", "/api/review", "/api/data-quality", "/api/uploads"]:
        payload = client.get(path).json()
        blob = str(payload)
        assert "GST inclusive" in blob, path


# --- per-manager detail -------------------------------------------------------

def test_manager_detail_grid_shape(client):
    """The AM sheet layout: transaction types by month, then forecast and budget."""
    d = client.get("/api/managers/Sam%20Stewart/detail?financial_year=2026").json()
    assert d["canonical_manager"] == "Sam Stewart"
    assert len(d["months"]) == 12
    assert d["months"][0] == "2026-07-01"
    assert d["months"][-1] == "2027-06-01"
    labels = [r["label"] for r in d["rows"]]
    for expected in ("Renewal", "Transfer Renewal", "New Business",
                     "Net Actual Income", "Renewal Forecast",
                     "Growth % applied", "New Business Growth Target",
                     "Total Budget", "Budget Achieved?",
                     "% Above / (Below) Target", "$ Above / (Below) Target",
                     "Prior Year Actual (same month)"):
        assert expected in labels, expected


def test_future_months_are_not_reported_as_unavailable(client):
    """A month that has not started is 'future', never 'unavailable'.

    Conflating the two made an early financial year look like a broken report.
    """
    d = client.get("/api/managers/Sam%20Stewart/detail?financial_year=2026").json()
    assert d["month_status"][0] == "completed"      # July 2026
    assert set(d["month_status"][1:]) == {"future"}

    net = next(r for r in d["rows"] if r["label"] == "Net Actual Income")
    assert net["cells"][0]["status"] == "actual"
    for c in net["cells"][1:]:
        assert c["status"] == "future"
        assert c["value"] is None
        assert "not started" in c["reason"]


def test_manager_detail_reconciles_to_the_views(client, conn):
    d = client.get("/api/managers/Sam%20Stewart/detail?financial_year=2026").json()
    net = next(r for r in d["rows"] if r["label"] == "Net Actual Income")
    assert cents(net["total"]) == cents(scalar(conn, """
        SELECT SUM(net_actual_income) FROM v_actual_month
        WHERE canonical_manager='Sam Stewart' AND financial_year=2026"""))
    assert cents(d["ytd_actual"]["value"]) == cents(net["total"])

    budget = next(r for r in d["rows"] if r["label"] == "Total Budget")
    assert cents(budget["total"]) == cents(scalar(conn, """
        SELECT SUM(total_budget) FROM v_monthly_budget
        WHERE canonical_manager='Sam Stewart' AND financial_year=2026"""))


def test_manager_detail_transaction_rows_sum_to_net(client):
    """The grid must add up: transaction types sum to Net Actual Income."""
    d = client.get("/api/managers/Sam%20Stewart/detail?financial_year=2026").json()
    txn = [r for r in d["rows"] if r["kind"] == "transaction"]
    total = sum(Decimal(str(r["total"])) for r in txn if r["total"] is not None)
    net = next(r for r in d["rows"] if r["label"] == "Net Actual Income")
    assert cents(total) == cents(net["total"])


def test_comparison_table_marks_unstarted_periods(client):
    rows = client.get("/api/managers?period=quarter&financial_year=2026").json()["items"]
    q1 = [r for r in rows if r["financial_quarter"] == 1]
    q4 = [r for r in rows if r["financial_quarter"] == 4]
    assert all(r["has_started"] for r in q1)
    assert not any(r["has_started"] for r in q4)


def test_unknown_manager_is_rejected(client):
    assert client.get("/api/managers/Nobody/detail").status_code == 404


# --- analytics ----------------------------------------------------------------

def test_year_over_year_is_like_for_like(client, conn):
    """Prior year is cut at the same month, so a part year is never compared
    with a full one."""
    d = client.get("/api/analytics/year-over-year?financial_year=2026").json()
    ytd = Decimal(str(d["ytd_actual"]["value"]))
    prior = Decimal(str(d["ytd_prior_year"]["value"]))
    assert cents(ytd) == cents(scalar(conn, """
        SELECT SUM(net_actual_income) FROM v_actual_month
        WHERE financial_year=2026 AND period_month <= DATE '2026-07-01'"""))
    assert cents(prior) == cents(scalar(conn, """
        SELECT SUM(net_actual_income) FROM v_actual_month
        WHERE financial_year=2025 AND period_month <= DATE '2025-07-01'"""))
    assert cents(Decimal(str(d["ytd_growth"]["value"]))) == cents(ytd - prior)


def test_budget_verdict_states_over_or_under(client):
    d = client.get("/api/analytics/year-over-year?financial_year=2026").json()
    assert d["on_track"] in (True, False)
    assert ("over" in d["verdict"]) or ("under" in d["verdict"])
    assert "%" in d["verdict"]


def test_future_months_carry_no_actual_in_the_series(client):
    d = client.get("/api/analytics/year-over-year?financial_year=2026").json()
    for m in d["months"]:
        if not m["started"]:
            assert m["net_actual"] is None, m["month"]


def test_manager_matrix_totals_match_the_views(client, conn):
    d = client.get("/api/analytics/manager-matrix?financial_year=2026"
                   "&measure=net_actual&include_non_ranked=true").json()
    assert cents(d["grand_total"]) == cents(scalar(conn, """
        SELECT SUM(net_actual_income) FROM v_actual_month WHERE financial_year=2026"""))
    for r in d["rows"]:
        row_sum = sum(Decimal(str(c["value"])) for c in r["cells"] if c["value"] is not None)
        if r["total"] is not None:
            assert cents(row_sum) == cents(r["total"]), r["canonical_manager"]


def test_return_analysis_shares_sum_to_one(client, conn):
    d = client.get("/api/analytics/return-income").json()
    total = Decimal(str(d["total_return_income"]))
    parts = sum(Decimal(str(i["amount"])) for i in d["items"])
    assert cents(parts) == cents(total)
    shares = sum(Decimal(str(i["share_of_returns"])) for i in d["items"])
    assert abs(shares - 1) < Decimal("0.0001")
    assert cents(total) == cents(scalar(conn, """
        SELECT SUM(absolute_return_income) FROM v_actual_month"""))


def test_return_rate_relates_returns_to_positive_income(client):
    d = client.get("/api/analytics/return-income").json()
    expected = Decimal(str(d["total_return_income"])) / Decimal(str(d["positive_income"]))
    assert abs(Decimal(str(d["return_rate"])) - expected) < Decimal("0.000001")


def test_manager_growth_override_changes_only_that_manager(admin, conn):
    """The per-manager growth control must not move anyone else's budget."""
    before = {r["canonical_manager"]: r["total_budget"] for r in
              admin.get("/api/budget?financial_year=2026").json()["quarters"]
              if r["financial_quarter"] == 2}
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager", "canonical_manager": "Liam Thornton",
        "financial_year": 2026, "growth_pct": 0.15,
        "reason": "per-manager control test"})
    assert res.status_code == 200
    try:
        after = {r["canonical_manager"]: r["total_budget"] for r in
                 admin.get("/api/budget?financial_year=2026").json()["quarters"]
                 if r["financial_quarter"] == 2}
        assert after["Liam Thornton"] > before["Liam Thornton"]
        for name, value in before.items():
            if name != "Liam Thornton":
                assert after[name] == value, name
        # The Original Forecast must be untouched by a budget change.
        assert cents(scalar(conn, """SELECT SUM(forecast_contribution)
                                     FROM original_forecast
                                     WHERE financial_year=2026""")) == Decimal("3677092.30")
    finally:
        with conn.cursor() as cur:
            cur.execute("""DELETE FROM growth_rate WHERE created_by='pytest-admin'""")
            cur.execute("DELETE FROM budget_audit WHERE performed_by='pytest-admin'")
        conn.commit()


# --- achievement measured on elapsed months -----------------------------------

def test_achievement_uses_budget_for_elapsed_months_only(client):
    """A quarter one month in is measured against one month of budget.

    Comparing July actuals with a whole-quarter budget reported every manager at
    roughly a third of target, which is arithmetic rather than performance.
    """
    rows = client.get("/api/managers?period=quarter&financial_year=2026").json()["items"]
    q1 = [r for r in rows if r["financial_quarter"] == 1 and r["has_started"]]
    assert q1
    measured = 0
    for r in q1:
        assert r["months_elapsed"] == 1
        if not (r["budget_to_date"]["available"] and r["total_budget"]["available"]):
            continue  # a manager with no budget for the quarter
        measured += 1
        assert (Decimal(str(r["budget_to_date"]["value"]))
                < Decimal(str(r["total_budget"]["value"]))), r["canonical_manager"]
        expected = (Decimal(str(r["net_actual_income"]["value"]))
                    / Decimal(str(r["budget_to_date"]["value"])))
        assert abs(Decimal(str(r["budget_achievement"]["value"])) - expected) < Decimal("0.0001")
    assert measured >= 10


def test_budget_verdict_is_explicit(client):
    rows = client.get("/api/managers?period=quarter&financial_year=2026").json()["items"]
    started = [r for r in rows if r["has_started"]]
    assert started
    for r in started:
        assert r["budget_verdict"] in ("Made budget", "Below budget", "Not measurable")
        if r["budget_verdict"] == "Made budget":
            assert Decimal(str(r["over_or_under_pct"]["value"])) >= 0
        elif r["budget_verdict"] == "Below budget":
            assert Decimal(str(r["over_or_under_pct"]["value"])) < 0


def test_renewal_achievement_no_longer_requires_policy_matching(client, conn):
    """Manager-month renewal achievement works from the first upload."""
    rows = client.get("/api/managers?period=quarter&financial_year=2026").json()["items"]
    started = [r for r in rows if r["has_started"]]
    measurable = [r for r in started if r["renewal_achievement"]["available"]]
    assert len(measurable) >= 10, "renewal achievement should be broadly available"

    sam = next(r for r in started if r["canonical_manager"] == "Sam Stewart")
    expected = scalar(conn, """
        SELECT renewal_achievement FROM v_renewal_income_month
        WHERE canonical_manager='Sam Stewart' AND period_month = DATE '2026-07-01'""")
    assert abs(Decimal(str(sam["renewal_achievement"]["value"])) - expected) < Decimal("0.0001")


def test_renewal_income_reconciles_to_transactions(conn):
    total = scalar(conn, """SELECT SUM(renewal_income) FROM v_renewal_income_month
                            WHERE financial_year = 2026""")
    assert cents(total) == cents(scalar(conn, """
        SELECT SUM(actual_income) FROM v_sales_reported
        WHERE financial_year = 2026 AND category IN ('RWL','TRW')"""))


def test_started_periods_sort_before_unstarted(client):
    rows = client.get("/api/managers?period=quarter&financial_year=2026").json()["items"]
    first_unstarted = next((i for i, r in enumerate(rows) if not r["has_started"]), len(rows))
    assert all(r["has_started"] for r in rows[:first_unstarted])
    assert not any(r["has_started"] for r in rows[first_unstarted:])
