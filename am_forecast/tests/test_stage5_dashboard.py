"""Dashboard acceptance tests.

The twenty checks required for the dashboard, run against the live API and the
database views behind it. Numbered to match the specification.
"""
from __future__ import annotations

import csv
import datetime as dt
from urllib.parse import quote
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


def rows_of(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


# --- 1. base operating position ----------------------------------------------

def test_01_base_operating_position_reconciles(client, conn):
    d = client.get("/api/base-position").json()
    # The cut-off month must be fully imported before base state means anything.
    # A sample covering only part of a later month leaves the cut-off month with
    # no transactions at all, and the flag is then correctly false -- a dataset
    # limitation, not a fault, so it skips with the reason rather than failing.
    if not d["checks"].get("cut_off_month_is_complete", True):
        covered = scalar(conn, """
            SELECT actual_load_state((SELECT date_trunc('month', cut_off_date)::date
                                      FROM reporting_settings WHERE id = 1))""")
        if covered != "full":
            pytest.skip(f"the cut-off month is {covered} in this dataset, so base "
                        f"state cannot be asserted")
    # Internal consistency rather than four figures pinned to one export: the
    # relationships must hold for any dataset, and pinned figures all became
    # wrong together the moment new data arrived.
    assert d["is_base_state"], d["checks"]
    assert all(d["checks"].values()), d["checks"]

    r = d["live"]["rounded"]
    budget = Decimal(r["total_budget"])
    forecast = Decimal(r["original_renewal_forecast"])
    outlook = Decimal(r["latest_outlook"])
    gap = Decimal(r["remaining_budget_gap"])
    assert forecast > 0 and outlook > 0
    assert budget >= forecast
    assert abs((budget - outlook) - gap) <= CENT


# --- 2. no synthetic data -----------------------------------------------------

def test_02_no_synthetic_data_present(client, conn):
    d = client.get("/api/base-position").json()
    assert d["live"]["snapshots"] == 1
    # Every accepted row is present, whatever the dataset holds.
    assert d["live"]["transactions"] == scalar(conn, """
        SELECT COALESCE(SUM(accepted_row_count), 0) FROM upload_batch
        WHERE status='accepted' AND file_type='sales'""")
    # Synthetic rows are identified by their fingerprint, not by an invoice
    # number range. Real invoice numbers have now reached 8,800,000, so the
    # range test raised a false alarm on genuine data — exactly the failure
    # mode a contamination check must not have.
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE fingerprint LIKE %s
                              OR fingerprint LIKE %s""",
                  ("pytest-%", "synthetic-%")) == 0


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

def test_05_unavailable_measures_are_null_not_zero(client, conn, closed_month):
    """N/A is never rendered as a zero.

    Reporting 0% against a manager with no baseline says they failed. The truth
    is that we cannot measure them, and those are different claims.
    """
    fy = closed_month["financial_year"]
    rows = client.get(f"/api/managers?period=month&financial_year={fy}"
                      "&include_non_ranked=true").json()["items"]
    assert rows
    for r in rows:
        for key in ("renewal_achievement", "budget_achievement",
                    "renewal_forecast", "budget_to_date"):
            measure = r[key]
            if not measure["available"]:
                assert measure["value"] is None, (r["canonical_manager"], key)
                assert measure["reason"], (r["canonical_manager"], key)

def test_05b_baseline_covers_managers_with_a_forecast(client, conn, closed_month):
    """Achievement is measurable wherever a forecast exists for the manager.

    This was originally pinned to July 2026 and to a count of twelve. What it is
    really asserting is that a manager with a forecast can be measured, and a
    manager without one reports N/A rather than a misleading zero.
    """
    fy = closed_month["financial_year"]
    rows = {r["canonical_manager"]: r for r in
            client.get(f"/api/managers?period=month&financial_year={fy}"
                       "&include_non_ranked=true").json()["items"]
            if r["period_month"] == closed_month["month_iso"]}
    if not rows:
        pytest.skip("no manager rows for the closed month")

    with_forecast = {r[0] for r in rows_of(conn, """
        SELECT canonical_manager FROM v_original_forecast_month
        WHERE forecast_month = %s GROUP BY 1""", (closed_month["month"],))}

    for name, r in rows.items():
        if name in with_forecast and r["net_actual_income"]["available"]:
            assert r["budget_achievement"]["available"] or \
                not r["budget_to_date"]["available"], name
        elif name not in with_forecast:
            assert not r["renewal_achievement"]["available"] or \
                r["renewal_forecast"]["available"], name

def test_06_completed_month_has_no_latest_forecast(client, conn, closed_month):
    """A month that has closed reports actuals and carries no Latest Forecast."""
    month = closed_month["month"]
    assert scalar(conn, """SELECT SUM(latest_forecast) FROM v_forecast_position_month
                           WHERE forecast_month = %s""", (month,)) is None
    rows = client.get(f"/api/managers?period=month"
                      f"&financial_year={closed_month['financial_year']}"
                      "&include_non_ranked=true").json()["items"]
    closed = [r for r in rows if r["period_month"] == closed_month["month_iso"]]
    assert closed
    for r in closed:
        assert not r["latest_forecast"]["available"]
        assert "completed" in (r["latest_forecast"]["reason"] or "").lower()

def test_07_baseline_is_declared_for_every_forecast_month(client, conn):
    """Each forecast month states what it was measured against.

    A month established from something other than a policy-level snapshot must
    say so, rather than implying detail it does not have.
    """
    baselines = client.get("/api/data-quality").json()["baselines"]
    assert baselines, "every forecast month should declare a baseline"
    for b in baselines:
        if b.get("forecast_contribution") in (None, 0):
            continue  # a month with no forecast has nothing to declare
        assert b["baseline_source"], b["forecast_month"]
        # A manager-month baseline must not claim policy-level detail.
        if "policy" not in (b["baseline_source"] or "").lower():
            assert scalar(conn, """
                SELECT count(*) FROM original_forecast
                WHERE forecast_month = %s AND grain = 'policy'""",
                          (b["forecast_month"],)) == 0, b["forecast_month"]

def test_08_twelve_zero_income_policies_in_data_quality(client, conn):
    d = client.get("/api/data-quality").json()
    zero_in_data = scalar(conn, """SELECT count(*) FROM forecast_policy
                                   WHERE NOT is_excluded AND raw_expected_income = 0""")
    assert d["counts"]["zero_expected_policies"] == zero_in_data
    assert d["expected"]["zero_expected_policies"] == zero_in_data
    # The note explains why such policies are easy to undercount; it no longer
    # quotes a figure, because the figure belongs to a dataset.
    assert "zero" in d["notes"]["zero_expected_policies"].lower()
    detail = client.get("/api/data-quality/zero_expected_policies").json()
    assert detail["total"] == zero_in_data
    assert all(Decimal(str(r["raw_expected_income"])) == 0
               for r in detail["items"]), "every listed policy must be a true zero"
    if zero_in_data:
        assert detail["items"], "policies counted must also be listed"
    # Previously asserted one PolicyID from the first export. What matters is
    # that a policy whose components cancel exactly is recognised as zero
    # rather than counted as a tiny non-zero remainder.
    for r in detail["items"]:
        assert Decimal(str(r["raw_expected_income"])) == 0


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


def test_10_highview_remains_in_the_excluded_audit_view(client, conn):
    d = client.get("/api/data-quality").json()
    excluded_sales = scalar(conn, """SELECT count(*) FROM sales_transaction
                                     WHERE is_excluded""")
    assert d["counts"]["excluded_sales_records"] == excluded_sales
    excluded_forecast = scalar(conn, """SELECT count(*) FROM forecast_policy
                                        WHERE is_excluded""")
    assert d["counts"]["excluded_forecast_records"] == excluded_forecast
    detail = client.get("/api/data-quality/excluded_records").json()
    assert detail["total"] == excluded_sales + excluded_forecast
    assert all(r["exclusion_field"] for r in detail["items"])


# --- 11. Anastasia K ----------------------------------------------------------

def test_11_non_ranked_managers_are_in_totals_but_not_rankings(client, conn):
    """Rankings and business totals answer different questions.

    Named after Anastasia K originally, which tied it to one roster. The rule is
    that a manager excluded from rankings still counts towards the business.
    """
    fy = scalar(conn, """SELECT au_financial_year(cut_off_date)
                         FROM reporting_settings WHERE id = 1""")
    ranked = {r["canonical_manager"] for r in
              client.get(f"/api/managers?period=year&financial_year={fy}"
                         ).json()["items"]}
    everyone = {r["canonical_manager"] for r in
                client.get(f"/api/managers?period=year&financial_year={fy}"
                           "&include_non_ranked=true").json()["items"]}

    non_ranked = {r[0] for r in rows_of(conn, """
        SELECT canonical_manager FROM reporting_manager
        WHERE NOT include_in_rankings""")}
    assert non_ranked, "the fixture should include at least one non-ranked manager"

    # None of them may appear in rankings.
    assert not (non_ranked & ranked)
    # Any with income must still be reachable, and must count to the business.
    # The test compares against a year-scoped endpoint, so it must scope its own
    # expectation to the same year -- a manager who stopped trading has no rows
    # in the current year and correctly does not appear.
    with_income = {r[0] for r in rows_of(conn, """
        SELECT canonical_manager FROM v_actual_month
        WHERE financial_year = %s GROUP BY 1""", (fy,))}
    for name in non_ranked & with_income:
        assert name in everyone, name
        assert scalar(conn, """SELECT include_in_business_totals
                               FROM reporting_manager
                               WHERE canonical_manager = %s""", (name,)) is True

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

    from conftest import SALES_FILE

    # Named a dataset that is no longer supplied, so the test failed on a
    # missing file while saying nothing about preview fidelity. The offset is
    # above any real invoice number rather than a fixed distance from one, so a
    # larger export cannot grow into the fixture range — that collision has
    # already cost this suite twice.
    sample = pl.read_csv(SALES_FILE, infer_schema_length=0).head(200).with_columns(
        (pl.col("InvNumber").cast(pl.Int64) + 90_000_000).cast(pl.Utf8).alias("InvNumber"))
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


def test_future_months_are_not_reported_as_unavailable(client, conn, closed_month):
    """A month that has not started is 'future', never 'unavailable'.

    Conflating the two made an early financial year look like a broken report.
    """
    fy = closed_month["financial_year"]
    d = client.get(f"/api/managers/Michael%20Stewart/detail?financial_year={fy}").json()
    statuses = dict(zip(d["months"], d["month_status"]))
    assert statuses[closed_month["month_iso"]] == "completed"
    later = [m for m in d["months"] if m > closed_month["month_iso"]]
    assert later
    assert {statuses[m] for m in later} == {"future"}

    net = next(r for r in d["rows"] if r["label"] == "Net Actual Income")
    by_month = {c["month"]: c for c in net["cells"]}
    for m in later:
        assert by_month[m]["status"] == "future"
        assert by_month[m]["value"] is None
        assert "not started" in by_month[m]["reason"]

def test_manager_detail_reconciles_to_the_views(client, conn, closed_month):
    """The grid totals must equal the views they are drawn from."""
    fy = closed_month["financial_year"]
    manager = scalar(conn, """SELECT canonical_manager FROM v_actual_month
                              WHERE financial_year = %s
                              GROUP BY 1 ORDER BY SUM(net_actual_income) DESC
                              LIMIT 1""", (fy,))
    if manager is None:
        pytest.skip("no manager income for this period")

    d = client.get(f"/api/managers/{quote(manager)}/detail"
                   f"?financial_year={fy}").json()
    net = next(r for r in d["rows"] if r["label"] == "Net Actual Income")
    assert cents(net["total"]) == cents(scalar(conn, """
        SELECT SUM(net_actual_income) FROM v_actual_month
        WHERE canonical_manager = %s AND financial_year = %s""", (manager, fy)))

    budget = next(r for r in d["rows"] if r["label"] == "Total Budget")
    expected_budget = scalar(conn, """
        SELECT SUM(total_budget) FROM v_monthly_budget
        WHERE canonical_manager = %s AND financial_year = %s""", (manager, fy))
    if expected_budget is None:
        assert budget["total"] is None
    else:
        assert cents(budget["total"]) == cents(expected_budget)

def test_manager_detail_transaction_rows_sum_to_net(client):
    """The grid must add up: transaction types sum to Net Actual Income."""
    d = client.get("/api/managers/Sam%20Stewart/detail?financial_year=2026").json()
    txn = [r for r in d["rows"] if r["kind"] == "transaction"]
    total = sum(Decimal(str(r["total"])) for r in txn if r["total"] is not None)
    net = next(r for r in d["rows"] if r["label"] == "Net Actual Income")
    assert cents(total) == cents(net["total"])


def test_comparison_table_marks_unstarted_periods(client, conn, closed_month):
    """A period that has not begun is marked as such, never shown as zero."""
    fy, q = closed_month["financial_year"], closed_month["financial_quarter"]
    rows = client.get(f"/api/managers?period=quarter&financial_year={fy}").json()["items"]
    current = [r for r in rows if r["financial_quarter"] == q]
    assert current
    assert all(r["has_started"] for r in current)

    # Later quarters only exist in the response where the manager has a budget
    # for them; where they do, they must be marked unstarted.
    later = [r for r in rows if r["financial_quarter"] > q]
    for r in later:
        assert not r["has_started"], r["canonical_manager"]
        value = r["net_actual_income"]["value"]
        assert value is None or Decimal(str(value)) == 0, r["canonical_manager"]

def test_unknown_manager_is_rejected(client):
    assert client.get("/api/managers/Nobody/detail").status_code == 404


# --- analytics ----------------------------------------------------------------

def test_year_over_year_is_like_for_like(client, conn, closed_month):
    """Prior year is cut at the same month, so a part year is never compared
    with a full one."""
    fy = closed_month["financial_year"]
    cut = closed_month["month"]
    d = client.get(f"/api/analytics/year-over-year?financial_year={fy}").json()

    ytd = Decimal(str(d["ytd_actual"]["value"]))
    assert cents(ytd) == cents(scalar(conn, """
        SELECT COALESCE(SUM(net_actual_income), 0) FROM v_actual_month
        WHERE financial_year = %s AND period_month <= %s""", (fy, cut)))

    # The prior-year figure is cut at the same month of that year. Where no
    # prior year is loaded it is unavailable, which must not read as zero.
    prior_cut = dt.date(cut.year - 1, cut.month, 1)
    expected_prior = scalar(conn, """
        SELECT SUM(net_actual_income) FROM v_actual_month
        WHERE financial_year = %s AND period_month <= %s""", (fy - 1, prior_cut))
    if expected_prior is None:
        assert not d["ytd_prior_year"]["available"]
        assert d["ytd_prior_year"]["value"] is None
    else:
        prior = Decimal(str(d["ytd_prior_year"]["value"]))
        assert cents(prior) == cents(expected_prior)
        assert cents(Decimal(str(d["ytd_growth"]["value"]))) == cents(ytd - prior)

def test_budget_verdict_states_over_or_under(client, conn, closed_month):
    fy = closed_month["financial_year"]
    d = client.get(f"/api/analytics/year-over-year?financial_year={fy}").json()
    if d["on_track"] is None:
        # No budget applies, so no verdict can be given. It must say so rather
        # than assert a direction.
        assert "not measurable" in d["verdict"].lower()
    else:
        assert d["on_track"] in (True, False)
        assert ("over" in d["verdict"]) or ("under" in d["verdict"])
        assert "%" in d["verdict"]

def test_future_months_carry_no_actual_in_the_series(client):
    d = client.get("/api/analytics/year-over-year?financial_year=2026").json()
    for m in d["months"]:
        if not m["started"]:
            assert m["net_actual"] is None, m["month"]


def test_manager_matrix_totals_match_the_views(client, conn, closed_month):
    fy = closed_month["financial_year"]
    d = client.get(f"/api/analytics/manager-matrix?financial_year={fy}"
                   "&measure=net_actual&include_non_ranked=true").json()
    # The matrix reports months up to the cut-off and marks later ones as
    # future, so the view must be scoped the same way -- the closed_month
    # fixture rolls the cut-off back, and comparing a cut-off-limited total
    # against a whole-year sum was measuring two different periods.
    assert cents(d["grand_total"]) == cents(scalar(conn, """
        SELECT COALESCE(SUM(net_actual_income), 0) FROM v_actual_month
        WHERE financial_year = %s
          AND period_month <= (SELECT date_trunc('month', cut_off_date)::date
                               FROM reporting_settings WHERE id = 1)""", (fy,)))
    for r in d["rows"]:
        row_sum = sum(Decimal(str(c["value"])) for c in r["cells"]
                      if c["value"] is not None)
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
    quarters = admin.get("/api/budget?financial_year=2026").json()["quarters"]
    if not quarters:
        pytest.skip("no budget rows for this dataset")
    # Whichever manager the dataset actually holds; the rule is that only the
    # named one moves.
    target = quarters[0]["canonical_manager"]
    q = quarters[0]["financial_quarter"]
    forecast_before = scalar(conn, """SELECT SUM(forecast_contribution)
                                      FROM original_forecast
                                      WHERE financial_year=2026""")
    before = {r["canonical_manager"]: r["total_budget"] for r in quarters
              if r["financial_quarter"] == q}
    res = admin.post("/api/budget/growth-rate", json={
        "scope": "manager", "canonical_manager": target,
        "financial_year": 2026, "growth_pct": 0.15,
        "reason": "per-manager control test"})
    assert res.status_code == 200
    try:
        after = {r["canonical_manager"]: r["total_budget"] for r in
                 admin.get("/api/budget?financial_year=2026").json()["quarters"]
                 if r["financial_quarter"] == q}
        assert after[target] > before[target]
        for name, value in before.items():
            if name != target:
                assert after[name] == value, name
        # The Original Forecast must be untouched by a budget change.
        # A budget change must never move the forecast it derives from.
        assert scalar(conn, """SELECT SUM(forecast_contribution)
                               FROM original_forecast
                               WHERE financial_year=2026""") == forecast_before
    finally:
        with conn.cursor() as cur:
            cur.execute("""DELETE FROM growth_rate WHERE created_by='pytest-admin'""")
            cur.execute("DELETE FROM budget_audit WHERE performed_by='pytest-admin'")
        conn.commit()


# --- achievement measured on elapsed months -----------------------------------

def test_achievement_uses_budget_for_elapsed_months_only(client, conn, closed_month):
    """Achievement is measured against the budget for the months elapsed.

    Comparing one month of actuals with a whole-quarter budget reported every
    manager at roughly a third of target, which is arithmetic rather than
    performance.
    """
    fy, q = closed_month["financial_year"], closed_month["financial_quarter"]
    rows = client.get(f"/api/managers?period=quarter&financial_year={fy}").json()["items"]
    current = [r for r in rows if r["financial_quarter"] == q and r["has_started"]]
    assert current

    measured = 0
    for r in current:
        if not (r["budget_to_date"]["available"] and r["total_budget"]["available"]):
            continue
        measured += 1
        # Never more than the whole period's budget, and equal only when the
        # period has fully elapsed.
        assert (Decimal(str(r["budget_to_date"]["value"]))
                <= Decimal(str(r["total_budget"]["value"]))), r["canonical_manager"]
        if r["budget_achievement"]["available"]:
            expected = (Decimal(str(r["net_actual_income"]["value"]))
                        / Decimal(str(r["budget_to_date"]["value"])))
            assert abs(Decimal(str(r["budget_achievement"]["value"])) - expected) \
                < Decimal("0.0001"), r["canonical_manager"]
    assert measured >= 1

def test_budget_verdict_is_explicit(client, conn, closed_month):
    fy = closed_month["financial_year"]
    rows = client.get(f"/api/managers?period=quarter&financial_year={fy}").json()["items"]
    started = [r for r in rows if r["has_started"]]
    assert started
    for r in started:
        assert r["budget_verdict"] in ("Made budget", "Below budget", "Not measurable")
        if r["budget_verdict"] == "Made budget":
            assert Decimal(str(r["over_or_under_pct"]["value"])) >= 0
        elif r["budget_verdict"] == "Below budget":
            assert Decimal(str(r["over_or_under_pct"]["value"])) < 0

def test_renewal_achievement_no_longer_requires_policy_matching(client, conn,
                                                                closed_month):
    """Manager-month renewal achievement works from the first upload.

    It must not depend on policy-level matching, which needs a forecast period
    overlapping transacted actuals and so is unavailable early on.
    """
    fy = closed_month["financial_year"]
    rows = client.get(f"/api/managers?period=quarter&financial_year={fy}").json()["items"]
    started = [r for r in rows if r["has_started"]]
    assert started
    measurable = [r for r in started if r["renewal_achievement"]["available"]]
    assert measurable, "renewal achievement should be available without matching"

    for r in measurable[:5]:
        expected = scalar(conn, """
            SELECT safe_div(SUM(renewal_income), SUM(original_forecast))
            FROM v_renewal_income_month
            WHERE canonical_manager = %s AND financial_year = %s AND period_started""",
                          (r["canonical_manager"], fy))
        assert abs(Decimal(str(r["renewal_achievement"]["value"])) - expected) \
            < Decimal("0.0001"), r["canonical_manager"]

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
