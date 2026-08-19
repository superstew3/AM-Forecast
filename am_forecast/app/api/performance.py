"""Performance: the two-ledger model, on a screen at last.

Migration 0018 built this and nothing ever read it. Seven views -- month
performance, the outlook, expected income, missing forecasts, baseline basis,
actual coverage -- all correct, all tested at the database level, and reachable
from no endpoint and no page.

The consequence was not cosmetic. Every rule agreed for that model was invisible:

    A month still running was scored as though it had finished, so a manager
    three weeks into a quarter read as failing.

    A month that began without a target said nothing, instead of Missing
    Forecast.

    A month whose transactions had never been imported showed $0.00 and 0%,
    which is indistinguishable from a month where nobody earned anything.

    Bonus figures carried no GST label, in a system where every other figure is
    GST inclusive -- a nine per cent discrepancy with nothing on screen to
    explain it.

This exposes them. The views already carry the wording; this endpoint passes it
through rather than reinventing it in the interface, so what the reader sees and
what the database means cannot drift apart.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from .core import (
    current_financial_year, current_user, fetch_all, fetch_one, meta,
)

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("")
def performance(financial_year: int | None = Query(None),
                manager: str | None = Query(None),
                user=Depends(current_user)):
    """Expected and actual side by side, per manager and month.

    Both ledgers for every month, never one substituted for the other: a
    transaction import can only move actual income, a forecast upload can only
    move expected income, and the page shows both rather than choosing.
    """
    financial_year = financial_year or current_financial_year()
    params: dict = {"fy": financial_year}
    clause = ""
    if manager:
        clause = " AND canonical_manager = %(manager)s"
        params["manager"] = manager

    months = fetch_all(f"""
        SELECT canonical_manager, month, financial_year, financial_quarter,
               month_state, forecast_income, target_income, uplift_applied,
               actual_income, actual_income_to, variance, achievement_pct,
               status, status_note, actuals_load_state, basis_scoreable,
               transaction_rows
        FROM v_month_performance
        WHERE financial_year = %(fy)s{clause}
        ORDER BY canonical_manager, month""", params)

    quarters = fetch_all(f"""
        SELECT canonical_manager, financial_year, financial_quarter,
               forecast_income, target_income, actual_income,
               actual_income_scoreable, target_income_scoreable,
               latest_outlook, months_in_progress, months_missing_forecast,
               months_basis_unverified, achievement_pct_completed
        FROM v_performance_quarter
        WHERE financial_year = %(fy)s{clause}
        ORDER BY canonical_manager, financial_quarter""", params)

    outlook = fetch_all(f"""
        SELECT canonical_manager, month, month_state, outlook_income,
               outlook_basis, outlook_note
        FROM v_outlook_month_v2
        WHERE financial_year = %(fy)s{clause}
        ORDER BY canonical_manager, month""", params)

    return {
        "financial_year": financial_year,
        "current_month": fetch_one("SELECT reporting_current_month() AS m")["m"],
        "months": months,
        "quarters": quarters,
        "outlook": outlook,
        # Months that began with no target. A routine upload is not allowed to
        # fill these -- only an audited override -- so they are surfaced rather
        # than left to be noticed.
        "missing_forecast": fetch_all("""
            SELECT month, month_state, override_pending
            FROM v_missing_forecast_month ORDER BY month"""),
        # Which months have transactions imported, so "nobody earned anything"
        # and "nobody has uploaded it" can be told apart on screen.
        "coverage": fetch_all("""
            SELECT DISTINCT m.month, actual_load_state(m.month) AS load_state,
                   actual_loaded_to(m.month) AS loaded_to
            FROM (SELECT DISTINCT month FROM v_month_performance
                  WHERE financial_year = %(fy)s) m
            ORDER BY m.month""", {"fy": financial_year}),
        # Baselines still on the pre-associate basis. A month holding one is
        # excluded from achievement and bonus until it is reconstructed.
        "baseline_basis": fetch_all("""
            SELECT month, baseline_rows, rows_associate, rows_unverified,
                   value_unverified, scoreable
            FROM v_baseline_basis_month
            WHERE NOT scoreable ORDER BY month"""),
        "meta": meta(financial_year, notes=[
            "Actual income is month-to-date for the month under way. A month "
            "still running is never scored: it shows as in progress, because a "
            "whole month's target against a part month's income is not a result.",
            "Bonus payments are GST exclusive. Every other figure here is GST "
            "inclusive.",
        ]),
    }


@router.get("/months")
def performance_months(financial_year: int | None = Query(None),
                       user=Depends(current_user)):
    """The business rolled up by month, for a headline strip or a chart."""
    financial_year = financial_year or current_financial_year()
    return {
        "financial_year": financial_year,
        "months": fetch_all("""
            SELECT month, month_state,
                   MIN(actuals_load_state)                         AS actuals_load_state,
                   MAX(actual_income_to)                           AS actual_income_to,
                   SUM(forecast_income)                            AS forecast_income,
                   SUM(target_income)                              AS target_income,
                   SUM(actual_income)                              AS actual_income,
                   SUM(variance)                                   AS variance,
                   count(*) FILTER (WHERE status = 'achieved')     AS managers_achieved,
                   count(*) FILTER (WHERE status = 'below_target') AS managers_below,
                   count(*) FILTER (WHERE status = 'missing_forecast')
                                                                   AS managers_missing_forecast,
                   count(*)                                        AS managers,
                   -- One note for the month, taken from the rows themselves so
                   -- the wording cannot drift from what the data means.
                   MIN(status_note)                                AS status_note
            FROM v_month_performance
            WHERE financial_year = %(fy)s
            GROUP BY month, month_state ORDER BY month""",
            {"fy": financial_year}),
        "meta": meta(financial_year),
    }
