"""The operational status panel.

Two files a month is the whole routine this system asks of a person. Whether it
had actually been kept was answerable only by opening four screens and knowing
what a healthy answer looked like on each -- Uploads for the last file, the
performance page for load state, Settings for month locks, /api/health for
migrations. That is a skill, not a routine, and it decays.

These tests do not assert that the system is currently healthy. They assert that
what the panel SAYS agrees with what the database MEANS, whatever state the
database happens to be in. A test that restated "August is loaded" would go red
the first time somebody loaded September.

Every rule is read from the database and compared with the endpoint. Where the
two can disagree, that is the bug worth catching.
"""
from __future__ import annotations

import os

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


@pytest.fixture(scope="module")
def status(client):
    r = client.get("/api/status")
    assert r.status_code == 200, r.text
    return r.json()


def by_key(status: dict, key: str) -> dict:
    matches = [i for i in status["items"] if i["check_key"] == key]
    assert len(matches) == 1, f"expected exactly one '{key}' row, got {len(matches)}"
    return matches[0]


SEVERITIES = {"ok", "attention", "action"}


# --- shape -------------------------------------------------------------------

def test_every_check_says_what_is_wrong_and_what_to_do(status):
    """A severity with no instruction is a colour, not an answer.

    The point of the panel is that it does not require the reader to already
    know what a red means. Every row carries a headline, an explanation and an
    action, and none of them may be blank.
    """
    assert status["items"], "the panel returned no checks at all"
    for i in status["items"]:
        where = i["check_key"]
        assert i["severity"] in SEVERITIES, f"{where}: severity {i['severity']!r}"
        for field in ("title", "headline", "detail", "what_to_do"):
            assert i.get(field), f"{where}: {field} is empty"


def test_the_overall_verdict_is_the_worst_row(status):
    """Rolled up from the rows, not decided a second time.

    The sidebar badge and the summary line both read `overall`. If it were
    computed independently of the rows it summarises, the badge could read green
    over a page of red -- which is worse than no badge.
    """
    rank = {"ok": 0, "attention": 1, "action": 2}
    worst = max(status["items"], key=lambda i: rank[i["severity"]])["severity"]
    assert status["overall"] == worst

    for severity in SEVERITIES:
        assert status["counts"][severity] == sum(
            1 for i in status["items"] if i["severity"] == severity), severity


# --- one implementation, not two ---------------------------------------------

def test_the_panel_and_health_agree_about_migrations(client, status):
    """The anti-drift test, and the reason this one exists at all.

    Production sat five migrations behind while every publish reported success.
    Two places answering "is the schema current" is how a question comes to have
    two answers, so /api/health and /api/status share one implementation.

    This fails the moment somebody gives either of them a copy of its own.
    """
    health = client.get("/api/health").json()["migrations"]
    panel = status["migrations"]

    for key in ("auto_migrate_enabled", "files_found", "recorded_in_database",
                "outstanding", "database"):
        assert health.get(key) == panel.get(key), (
            f"/api/health and /api/status disagree about {key}: "
            f"{health.get(key)!r} vs {panel.get(key)!r}")

    row = by_key(status, "migrations")
    outstanding = panel.get("outstanding") or []
    assert (row["severity"] == "ok") == (not outstanding and not panel.get("error")), (
        "the migration row's severity does not match whether migrations are "
        "actually outstanding")


def test_the_status_view_never_reads_the_stored_cut_off(conn):
    """Structural, so it cannot rot quietly.

    Migration 0020 moved every view onto the calendar and seven API sites were
    missed, which is how an accepted August upload became invisible. A panel
    whose entire job is to say what needs attention must not be the last place
    still consulting a setting that decides nothing.

    Asserted against the catalogue rather than by reading the SQL, so it holds
    however the view is rewritten.
    """
    deps = {r[0] for r in _fetch(conn, """
        SELECT table_name FROM information_schema.view_table_usage
        WHERE view_name = 'v_operational_status'""")}
    assert "reporting_settings" not in deps, (
        "v_operational_status reads reporting_settings. Month boundaries come "
        "from reporting_current_month(); the cut-off has decided nothing since "
        "migration 0020.")
    assert deps, "the view has no dependencies at all; is it still there?"


def _fetch(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


# --- each check against the rule it reports ----------------------------------

def test_sales_check_agrees_with_the_load_state(conn, status):
    """Green if and only if the database says the closed month is fully loaded.

    Read from actual_load_state() rather than restated here. The panel is
    allowed to be amber or red for its own reasons -- the file being merely due
    is not a fault -- but it may never be green over a month the database says
    is not complete.
    """
    state = scalar(conn, """
        SELECT actual_load_state((reporting_current_month()
                                  - INTERVAL '1 month')::date)""")
    row = by_key(status, "sales_actuals")
    assert (row["severity"] == "ok") == (state == "full"), (
        f"the panel reports {row['severity']} while the last completed month "
        f"is '{state}'")


def test_missing_forecast_is_about_the_forecast_not_the_budget(conn, status):
    """The distinction that made this panel wrong on its first pass.

    v_monthly_budget is absent for a month whose forecast is perfectly fine but
    whose growth rate will not resolve: resolve_growth_month() returns no rows
    when growth_rate holds nothing active at any scope, and the CROSS JOIN
    LATERAL then drops the month. Testing "is there a forecast" against it
    reports a confident red on a month that has one.

    The two conditions have completely different fixes -- an audited override
    and an upload, versus a rate set on the Budget page -- so they are two
    checks, and no month may appear in both.
    """
    expected = scalar(conn, """
        SELECT count(*) FROM generate_series(
                 make_date(au_financial_year(reporting_current_month()), 7, 1),
                 reporting_current_month(), INTERVAL '1 month') m
        WHERE NOT EXISTS (SELECT 1 FROM v_original_forecast_month f
                          WHERE f.forecast_month = m::date)""")
    assert by_key(status, "missing_forecast")["item_count"] == expected

    overlap = scalar(conn, """
        SELECT count(*) FROM generate_series(
                 make_date(au_financial_year(reporting_current_month()), 7, 1),
                 (reporting_current_month() + INTERVAL '1 month')::date,
                 INTERVAL '1 month') m
        WHERE NOT EXISTS (SELECT 1 FROM v_original_forecast_month f
                          WHERE f.forecast_month = m::date)
          AND EXISTS     (SELECT 1 FROM v_original_forecast_month f
                          WHERE f.forecast_month = m::date)""")
    assert overlap == 0, "a month is reported as both missing and present"


def test_the_growth_rate_check_catches_a_forecast_with_no_target(conn, status):
    """A forecast that produces no budget is invisible everywhere else.

    Target, achievement, bonus and the outlook gap are all simply absent for
    such a month, and no other page says why.
    """
    expected = scalar(conn, """
        SELECT count(*) FROM generate_series(
                 make_date(au_financial_year(reporting_current_month()), 7, 1),
                 (reporting_current_month() + INTERVAL '1 month')::date,
                 INTERVAL '1 month') m
        WHERE EXISTS     (SELECT 1 FROM v_original_forecast_month f
                          WHERE f.forecast_month = m::date)
          AND NOT EXISTS (SELECT 1 FROM v_monthly_budget b
                          WHERE b.forecast_month = m::date)""")
    row = by_key(status, "budget_resolution")
    assert row["item_count"] == expected
    assert (row["severity"] == "ok") == (expected == 0)


def test_next_month_is_judged_before_it_starts(conn, status):
    """The one warning that arrives while it is still cheap to act on.

    A month with no target is fixable by an upload right up until it begins, and
    only by an audited override afterwards. Warning about it on the 1st is
    warning after the fact.
    """
    has_forecast = scalar(conn, """
        SELECT EXISTS (SELECT 1 FROM v_original_forecast_month
                       WHERE forecast_month
                             = (reporting_current_month() + INTERVAL '1 month')::date)""")
    row = by_key(status, "next_month_forecast")
    assert (row["severity"] == "ok") == bool(has_forecast)


def test_a_part_loaded_month_behind_the_frontier_is_reported(conn, status):
    """A gap in the middle of the imports, which no later file will close.

    Distinct from the routine running late: the last completed month is
    deliberately excluded here because it has its own check.
    """
    expected = scalar(conn, """
        SELECT count(*) FROM generate_series(
                 make_date(au_financial_year(reporting_current_month()), 7, 1),
                 (reporting_current_month() + INTERVAL '1 month')::date,
                 INTERVAL '1 month') m
        WHERE m::date < (reporting_current_month() - INTERVAL '1 month')::date
          AND m::date >= date_trunc('month', lower(actual_coverage()))::date
          AND actual_load_state(m::date) <> 'full'""")
    row = by_key(status, "partial_months")
    assert row["item_count"] == expected
    assert (row["severity"] == "ok") == (expected == 0)


def test_the_renewals_check_reports_the_upload_not_a_guessed_pull_date(conn, status):
    """as_at is the upload date, and the panel says so.

    Nothing in the export states its own extract date, and
    forecast_snapshot.as_of_date is inferred from the earliest pending month, so
    it carries month resolution at best. Presenting either as "when it was
    pulled" would be a precision the system does not have.
    """
    last_upload = scalar(conn, """
        SELECT max(uploaded_at AT TIME ZONE 'Australia/Melbourne')::date
        FROM upload_batch WHERE file_type = 'renewals' AND status = 'accepted'""")
    row = by_key(status, "renewals_extract")
    if last_upload is None:
        assert row["as_at"] is None
        assert row["severity"] == "action"
    else:
        assert row["as_at"] == str(last_upload)


def test_a_due_date_is_never_in_the_past(conn, status):
    """"Next due" is the next one, not the one that was missed.

    A due date behind today reads as an overdue instruction and would send
    somebody looking for a file they cannot pull.
    """
    today = scalar(conn, "SELECT (now() AT TIME ZONE 'Australia/Melbourne')::date")
    for i in status["items"]:
        if i["next_due"]:
            assert str(i["next_due"]) >= str(today), (
                f"{i['check_key']} says the next one is due {i['next_due']}, "
                f"which has already passed")
