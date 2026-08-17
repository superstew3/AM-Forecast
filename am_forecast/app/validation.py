"""Source reconciliation figures and the income basis.

These were originally pinned to the first export ever loaded. That worked while
there was one dataset and quietly became wrong the moment a second arrived:
every expected figure referred to a file the system no longer held, so a
reconciliation failure said nothing about whether the import was correct.

They are now derived from the accepted batches. The check that matters is not
"does this equal 14,886" but "does what we reported equal what the file
contained, once exclusions are applied" — and that holds for any dataset.

The one genuinely fixed fact is kept and explained below: a policy whose
components cancel exactly is a zero, and floating-point arithmetic disagrees.
"""
from __future__ import annotations

from decimal import Decimal

TOLERANCE = Decimal("0.01")
CENT = Decimal("0.01")

# The single most consequential definition in the system, kept somewhere
# findable rather than buried in a migration.
INCOME_BASIS = {
    "sales": "PrimaryAssocAmount",
    "sales_gst": "GST inclusive; this report carries no tax column",
    "renewals": "PrimaryAssocCommSum + PrimaryAssocCommTaxSum",
    "renewals_gst": "the Sum column is GST exclusive; TaxSum is its GST",
    "note": (
        "The brokerage is the primary associate on these policies, so reported "
        "income is the associate share rather than gross commission and fees. "
        "The gross figures are retained on every row as gross_income and "
        "gross_expected_income, for audit and for reconciliation against the "
        "source report."),
}

ZERO_EXPECTED_EXPLANATION = (
    "A policy whose commission and fees cancel exactly has precisely zero "
    "expected income in decimal arithmetic. A floating-point pipeline returns a "
    "tiny non-zero remainder and counts the row as non-zero, which is how such "
    "policies come to be undercounted. No monetary total is affected either way."
)


def _scalar_row(conn, sql: str) -> tuple:
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()


def sales_expected(conn) -> dict:
    """What the sales side should reconcile to, from the accepted batches."""
    row = _scalar_row(conn, """
        SELECT COALESCE(SUM(source_row_count), 0),
               COALESCE(SUM(excluded_row_count), 0),
               COALESCE(SUM(positive_income), 0),
               COALESCE(SUM(return_income), 0),
               COALESCE(SUM(net_income), 0)
        FROM upload_batch
        WHERE status = 'accepted' AND file_type = 'sales'
    """)
    out = dict(zip(("source_rows", "excluded_rows", "positive_income",
                    "return_income", "net_income"), row))
    out["included_rows"] = out["source_rows"] - out["excluded_rows"]
    return out


def renewals_expected(conn) -> dict:
    """What the renewals side should reconcile to."""
    source_rows, excluded_rows, contribution = _scalar_row(conn, """
        SELECT COALESCE(SUM(source_row_count), 0),
               COALESCE(SUM(excluded_row_count), 0),
               COALESCE(SUM(expected_forecast_income), 0)
        FROM upload_batch
        WHERE status = 'accepted' AND file_type = 'renewals'
    """)
    negative, zero, raw = _scalar_row(conn, """
        SELECT COUNT(*) FILTER (WHERE raw_expected_income < 0),
               COUNT(*) FILTER (WHERE raw_expected_income = 0),
               COALESCE(SUM(raw_expected_income), 0)
        FROM forecast_policy WHERE NOT is_excluded
    """)
    return {
        "source_rows": source_rows,
        "excluded_rows": excluded_rows,
        "included_rows": source_rows - excluded_rows,
        "negative_expected_rows": negative,
        "zero_expected_rows": zero,
        "raw_expected_income": raw,
        "forecast_contribution": contribution,
    }


def base_position(conn) -> dict:
    """The headline position, read live.

    Previously a hardcoded set of four figures asserted on every test run. That
    caught drift usefully, but only for one dataset: loading a new export made
    all four wrong at once and the check had to be rewritten by hand. What it
    was really guarding is that the four figures remain internally consistent —
    budget above forecast, gap equal to budget less outlook — and that is what
    is checked now.
    """
    fy, cut = _scalar_row(conn, """
        SELECT au_financial_year(cut_off_date), cut_off_date
        FROM reporting_settings WHERE id = 1
    """)
    forecast, = _scalar_row(conn, f"""
        SELECT COALESCE(SUM(forecast_contribution), 0)
        FROM original_forecast WHERE financial_year = {fy}
    """)
    budget, = _scalar_row(conn, f"""
        SELECT COALESCE(SUM(total_budget), 0)
        FROM v_budget_quarter WHERE financial_year = {fy}
    """)
    outlook, gap = _scalar_row(conn, f"""
        SELECT COALESCE(SUM(latest_outlook), 0),
               COALESCE(SUM(remaining_budget_gap), 0)
        FROM v_outlook_quarter WHERE financial_year = {fy}
    """)
    return {
        "financial_year": fy,
        "cut_off_date": cut,
        "original_renewal_forecast": forecast,
        "total_budget": budget,
        "latest_outlook": outlook,
        "remaining_budget_gap": gap,
    }
