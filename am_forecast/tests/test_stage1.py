"""Stage 1 acceptance tests.

Run against a database that has had the migrations, seed and validation harness
applied. These are the figures that must never drift; if ingest logic changes
and one of these fails, the build fails.

    pytest tests/test_stage1.py --dsn "host=/tmp port=5433 user=postgres dbname=am_forecast"
"""
from __future__ import annotations

from decimal import Decimal

import psycopg2
import pytest

CENT = Decimal("0.01")


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


# --- Section 18: source reconciliation ---------------------------------------

def test_sales_row_counts(conn):
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == 14886
    assert scalar(conn, "SELECT count(*) FROM sales_transaction WHERE is_excluded") == 2163
    assert scalar(conn, "SELECT count(*) FROM sales_transaction WHERE NOT is_excluded") == 12723


def test_sales_income_totals(conn):
    pos, ret, net = rows(conn, """
        SELECT SUM(positive_income), SUM(signed_return_income), SUM(actual_income)
        FROM sales_transaction WHERE NOT is_excluded""")[0]
    assert abs(pos - Decimal("5620647.70")) <= CENT
    assert abs(ret - Decimal("-659271.01")) <= CENT
    assert abs(net - Decimal("4961376.69")) <= CENT


def test_renewals_row_counts(conn):
    assert scalar(conn, "SELECT count(*) FROM forecast_policy") == 6749
    assert scalar(conn, "SELECT count(DISTINCT policy_id) FROM forecast_policy") == 6749
    assert scalar(conn, "SELECT count(*) FROM forecast_policy WHERE is_excluded") == 975
    assert scalar(conn, "SELECT count(*) FROM forecast_policy WHERE NOT is_excluded") == 5774


def test_renewals_income_totals(conn):
    raw, contrib = rows(conn, """
        SELECT SUM(raw_expected_income), SUM(forecast_contribution)
        FROM forecast_policy WHERE NOT is_excluded""")[0]
    assert abs(raw - Decimal("3352917.06")) <= CENT
    assert abs(contrib - Decimal("3354995.38")) <= CENT


def test_negative_expected_rows(conn):
    """Three negative rows, contributing zero, visible as exceptions."""
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded AND raw_expected_income < 0""") == 3
    assert scalar(conn, """SELECT COALESCE(SUM(forecast_contribution),0) FROM forecast_policy
                           WHERE NOT is_excluded AND raw_expected_income < 0""") == 0
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded AND 'negative_expected' = ANY(exception_flags)""") == 3


def test_zero_expected_rows_is_twelve_not_eleven(conn):
    """Twelve, not the eleven in the original brief.

    PolicyID 931173620 has Comm 206.73 / CommTax 20.68 offset by Fee -206.73 /
    FeeTax -20.68. In exact decimal arithmetic that is precisely zero. A float
    pipeline returns 7.1e-15 and counts it as non-zero, which is where the
    eleven came from. Totals are unaffected.
    """
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded AND raw_expected_income = 0""") == 12
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded AND policy_id = 931173620
                             AND raw_expected_income = 0""") == 1


# --- Section 20 tests 11 to 16: manager aliases -------------------------------

@pytest.mark.parametrize("source,canonical", [
    ("Sam Peninsula", "Sam Stewart"),
    ("Sam Stewart", "Sam Stewart"),
    ("MichaelPeninsula", "Michael Stewart"),
    ("Michael Stewart", "Michael Stewart"),
    ("Liam Peninsula", "Liam Thornton"),
    ("Liam Thornton", "Liam Thornton"),
    ("Shannen SIG", "Shannen Giles"),
    ("ShannenPeninsula", "Shannen Giles"),
    ("SIG Retail", "Retail"),
    ("Peninsula Retail", "Retail"),
    ("Thomasina T", "Thomasina Troumb"),
])
def test_manager_alias_resolution(conn, source, canonical):
    assert scalar(conn, """SELECT canonical_manager FROM v_manager_resolution
                           WHERE source_manager = %s""", (source,)) == canonical


def test_every_source_manager_resolves(conn):
    """No fact row may fall through the alias table unresolved."""
    unresolved = rows(conn, """
        SELECT DISTINCT source_manager FROM v_sales_reported WHERE canonical_manager IS NULL""")
    assert unresolved == []


# --- Section 20 tests 17 and 18: Highview exclusions --------------------------

def test_highview_excluded_by_associate_not_only_by_manager(conn):
    """Test 17: rows excluded on an associate field where the manager is not
    Cam Highview."""
    n = scalar(conn, """
        SELECT count(*) FROM sales_transaction
        WHERE is_excluded AND exclusion_field IN ('PrimaryAssocCode','SecondaryAssocCode')
          AND upper(source_manager) <> 'CAM HIGHVIEW'""")
    assert n > 0


def test_legitimate_cameron_stewart_retained(conn):
    """Test 18: non-Highview Cameron Stewart survives."""
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE NOT is_excluded AND source_manager='Cameron Stewart'""") == 15
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded AND source_manager='Cameron Stewart'""") == 5


# --- Section 20 tests 19 to 21: duplicate handling ----------------------------

def test_fingerprints_are_unique(conn):
    """Test 20: multiple legitimate lines sharing an invoice number stay separate."""
    assert scalar(conn, "SELECT count(DISTINCT fingerprint) FROM sales_transaction") == 14886
    assert scalar(conn, """SELECT count(*) FROM (
        SELECT invoice_number FROM sales_transaction
        GROUP BY invoice_number HAVING count(*) > 1) x""") > 0


def test_no_duplicate_transactions_present(conn):
    """Test 19, structural half: the loaded set carries no duplicates.

    The behavioural half — that re-uploading a cumulative file changes no total
    and only increments seen_count — is asserted in
    tests/test_stage2_import.py::test_accepting_a_reupload_changes_no_total,
    which performs the re-upload itself rather than depending on load order.
    """
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == 14886
    assert scalar(conn, "SELECT count(DISTINCT fingerprint) FROM sales_transaction") == 14886
    net = scalar(conn, """SELECT SUM(actual_income) FROM sales_transaction
                          WHERE NOT is_excluded""")
    assert abs(net - Decimal("4961376.69")) <= CENT


def test_same_policy_number_different_policy_id_preserved(conn):
    """Test 21: distinct PolicyIDs sharing client, policy number and expiry."""
    n = scalar(conn, """SELECT count(*) FROM (
        SELECT client_code, policy_number, expiry_date
        FROM forecast_policy WHERE NOT is_excluded
        GROUP BY 1,2,3 HAVING count(DISTINCT policy_id) > 1) x""")
    assert n == 3


# --- Section 20 tests 22 and 23 -----------------------------------------------

def test_no_month_has_negative_forecast(conn):
    """Test 5 and 22: a monthly forecast can never go negative."""
    worst = scalar(conn, """
        SELECT MIN(total) FROM (
          SELECT forecast_month, SUM(forecast_contribution) AS total
          FROM forecast_policy WHERE NOT is_excluded GROUP BY 1) x""")
    assert worst >= 0


def test_original_forecast_contribution_never_negative(conn):
    assert scalar(conn, """SELECT count(*) FROM original_forecast
                           WHERE forecast_contribution < 0""") == 0


# --- July 2026 baseline decision ----------------------------------------------

def test_july_original_comes_from_legacy_not_actuals(conn):
    grain, origin, amount = rows(conn, """
        SELECT grain, origin, SUM(forecast_contribution)
        FROM original_forecast WHERE forecast_month = DATE '2026-07-01'
        GROUP BY 1,2""")[0]
    assert grain == "manager_month"
    assert origin == "legacy_dashboard"
    assert abs(amount - Decimal("348149.67")) <= CENT


def test_no_original_forecast_is_derived_from_actuals(conn):
    """The result must never become the baseline."""
    assert scalar(conn, """SELECT count(*) FROM original_forecast
                           WHERE origin = 'derived_from_actuals'""") == 0


def test_july_residual_pending_policies_not_in_original(conn):
    """The two leftover July pending policies stay in Latest, flagged residual,
    and are not treated as a July baseline."""
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded AND forecast_month = DATE '2026-07-01'""") == 2
    assert scalar(conn, """SELECT count(*) FROM forecast_policy
                           WHERE NOT is_excluded AND forecast_month = DATE '2026-07-01'
                             AND 'residual_pending' = ANY(exception_flags)""") == 2
    assert scalar(conn, """SELECT count(*) FROM original_forecast
                           WHERE forecast_month = DATE '2026-07-01' AND grain = 'policy'""") == 0


def test_achievement_is_null_not_zero_where_baseline_unusable(conn):
    """The core of the July decision: N/A, never 0%."""
    for manager in ("Cameron Stewart", "Dinghy Scheme"):
        achievement, usable = rows(conn, """
            SELECT renewal_achievement, baseline_usable
            FROM v_renewal_performance_month
            WHERE period_month = DATE '2026-07-01' AND canonical_manager = %s""",
                                   (manager,))[0]
        assert usable is False
        assert achievement is None


def test_achievement_is_computed_where_baseline_usable(conn):
    achievement = scalar(conn, """
        SELECT renewal_achievement FROM v_renewal_performance_month
        WHERE period_month = DATE '2026-07-01' AND canonical_manager = 'Sam Stewart'""")
    assert achievement is not None and achievement > 0


def test_fy2025_26_months_before_november_have_no_baseline(conn):
    for month in ("2025-07-01", "2025-08-01", "2025-09-01", "2025-10-01"):
        status, suppress = rows(conn, """
            SELECT baseline_status, suppress_achievement FROM forecast_baseline
            WHERE forecast_month = %s""", (month,))[0]
        assert status == "unavailable"
        assert suppress is True


# --- Budget -------------------------------------------------------------------

def test_budget_is_forecast_plus_growth(conn):
    """Test 13: Total Budget = Original Renewal Forecast + growth target."""
    orig, target, total = rows(conn, """
        SELECT SUM(original_renewal_forecast), SUM(new_business_growth_target),
               SUM(total_budget)
        FROM v_budget_quarter WHERE financial_year = 2026""")[0]
    assert abs(orig - Decimal("3701892.60")) <= CENT
    assert abs(target - orig * Decimal("0.075")) <= Decimal("0.10")
    assert abs(total - (orig + target)) <= CENT


def test_anastasia_in_totals_but_not_budget_or_rankings(conn):
    assert scalar(conn, """SELECT SUM(net_actual_income) FROM v_actual_month
                           WHERE canonical_manager = 'Anastasia K'""") > 0
    assert scalar(conn, """SELECT count(*) FROM v_budget_quarter
                           WHERE canonical_manager = 'Anastasia K'""") == 0
    assert scalar(conn, """SELECT include_in_rankings FROM reporting_manager
                           WHERE canonical_manager = 'Anastasia K'""") is False
    assert scalar(conn, """SELECT include_in_business_totals FROM reporting_manager
                           WHERE canonical_manager = 'Anastasia K'""") is True


def test_growth_rate_override_affects_only_that_manager(conn):
    """Test 24."""
    before = {m: v for m, v in rows(conn, """
        SELECT canonical_manager, SUM(total_budget) FROM v_budget_quarter
        WHERE financial_year = 2026 GROUP BY 1""")}
    with conn.cursor() as cur:
        cur.execute("""INSERT INTO growth_rate
            (scope, canonical_manager, financial_year, financial_quarter, growth_pct,
             note, active, created_by)
            VALUES ('manager_quarter','Sam Stewart',2026,1,0.2000,'test',true,'pytest')""")
    after = {m: v for m, v in rows(conn, """
        SELECT canonical_manager, SUM(total_budget) FROM v_budget_quarter
        WHERE financial_year = 2026 GROUP BY 1""")}
    conn.rollback()
    assert after["Sam Stewart"] > before["Sam Stewart"]
    for m in before:
        if m != "Sam Stewart":
            assert after[m] == before[m]


# --- Partial period labelling -------------------------------------------------

def test_fy2024_25_labelled_partial(conn):
    status, months = rows(conn, """
        SELECT coverage_status, months_present FROM period_coverage
        WHERE financial_year = 2024 AND data_domain = 'actuals'""")[0]
    assert status == "partial"
    assert months == 2


def test_all_categories_mapped(conn):
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE business_classification = 'Unmapped'""") == 0
