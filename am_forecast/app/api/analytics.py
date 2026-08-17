"""Analytics for the dashboard.

Comparison and shape, rather than single figures: how this year is tracking
against last, month by month, and where the difference is coming from.

Still no financial logic here. Everything is selected from the views and
compared; the arithmetic is subtraction and division on figures the database
already computed.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from .core import GST_NOTE, Meta, Money, Ratio, current_user, fetch_all, fetch_one, meta

router = APIRouter()

FUTURE = "Period has not started yet."
NO_PRIOR = "No prior-year figure for this period."


def _fy_months(fy: int) -> list[dt.date]:
    return [dt.date(fy + (7 + i - 1) // 12, (7 + i - 1) % 12 + 1, 1) for i in range(12)]


def _cut_month() -> dt.date:
    return fetch_one("""SELECT date_trunc('month', cut_off_date)::date AS m
                        FROM reporting_settings WHERE id = 1""")["m"]


def _pct_change(now, before) -> Decimal | None:
    if now is None or before is None or before == 0:
        return None
    return (now - before) / abs(before)


class MonthPoint(BaseModel):
    month: dt.date
    label: str
    started: bool
    net_actual: Decimal | None = None
    positive_actual: Decimal | None = None
    return_income: Decimal | None = None
    budget: Decimal | None = None
    original_forecast: Decimal | None = None
    latest_forecast: Decimal | None = None
    prior_year_actual: Decimal | None = None
    variance_to_budget: Decimal | None = None
    achievement: Decimal | None = None


class YearOverYear(BaseModel):
    financial_year: int
    label: str
    prior_label: str
    months: list[MonthPoint]

    ytd_actual: Money
    ytd_prior_year: Money
    ytd_growth: Money
    ytd_growth_pct: Ratio
    ytd_budget: Money
    ytd_variance: Money
    ytd_achievement: Ratio
    on_track: bool | None = None
    verdict: str

    prior_year_full: Money
    full_year_budget: Money
    latest_outlook: Money
    remaining_gap: Money
    outlook_vs_prior_year_pct: Ratio

    growth_by_manager: list[dict]
    growth_by_type: list[dict]
    meta: Meta


@router.get("/analytics/year-over-year", response_model=YearOverYear,
            tags=["analytics"])
def year_over_year(financial_year: int = Query(2026), manager: str | None = None,
                   user=Depends(current_user)):
    """This year against last, month by month, with the difference explained."""
    cut = _cut_month()
    months = _fy_months(financial_year)
    params = {"fy": financial_year, "py": financial_year - 1, "mgr": manager}
    where = " AND canonical_manager = %(mgr)s" if manager else ""

    actual = {r["period_month"]: r for r in fetch_all(f"""
        SELECT period_month, SUM(net_actual_income) AS net,
               SUM(positive_actual_income) AS positive,
               SUM(absolute_return_income) AS returns
        FROM v_actual_month WHERE financial_year = %(fy)s{where}
        GROUP BY 1""", params)}
    prior_raw = {r["period_month"]: r["net"] for r in fetch_all(f"""
        SELECT period_month, SUM(net_actual_income) AS net
        FROM v_actual_month WHERE financial_year = %(py)s{where}
        GROUP BY 1""", params)}
    prior = {dt.date(m.year + 1, m.month, 1): v for m, v in prior_raw.items()}
    budget = {r["forecast_month"]: r["b"] for r in fetch_all(f"""
        SELECT forecast_month, SUM(total_budget) AS b FROM v_monthly_budget
        WHERE financial_year = %(fy)s{where} GROUP BY 1""", params)}
    forecast = {r["forecast_month"]: r for r in fetch_all(f"""
        SELECT forecast_month, SUM(original_forecast) AS orig,
               SUM(latest_forecast) AS latest
        FROM v_forecast_position_month WHERE financial_year = %(fy)s{where}
        GROUP BY 1""", params)}

    points = []
    for m in months:
        a = actual.get(m)
        f = forecast.get(m)
        b = budget.get(m)
        started = m <= cut
        net = a["net"] if a else None
        var = (net - b) if (started and net is not None and b is not None) else None
        ach = (net / b) if (started and net is not None and b) else None
        points.append(MonthPoint(
            month=m, label=m.strftime("%b %y"), started=started,
            net_actual=net if started else None,
            positive_actual=a["positive"] if (a and started) else None,
            return_income=a["returns"] if (a and started) else None,
            budget=b, original_forecast=f["orig"] if f else None,
            latest_forecast=f["latest"] if f else None,
            prior_year_actual=prior.get(m),
            variance_to_budget=var, achievement=ach))

    ytd = [m for m in months if m <= cut]
    ytd_actual = sum((actual[m]["net"] for m in ytd if m in actual), Decimal(0)) or None
    ytd_prior = sum((prior[m] for m in ytd if m in prior), Decimal(0)) or None
    ytd_budget = sum((budget[m] for m in ytd if m in budget), Decimal(0)) or None
    growth = (ytd_actual - ytd_prior) if (ytd_actual is not None
                                          and ytd_prior is not None) else None
    variance = (ytd_actual - ytd_budget) if (ytd_actual is not None
                                             and ytd_budget is not None) else None
    achievement = (ytd_actual / ytd_budget) if (ytd_actual is not None
                                                and ytd_budget) else None

    on_track = None if achievement is None else achievement >= 1
    if achievement is None:
        verdict = "Not measurable yet: no budget or no completed months."
    elif on_track:
        verdict = (f"Ahead of budget by {abs(variance):,.0f} "
                   f"({(achievement - 1) * 100:.1f}% over) for the months completed.")
    else:
        verdict = (f"Behind budget by {abs(variance):,.0f} "
                   f"({(1 - achievement) * 100:.1f}% under) for the months completed.")

    prior_full = fetch_one(f"""SELECT SUM(net_actual_income) AS t FROM v_actual_month
                               WHERE financial_year = %(py)s{where}""", params)["t"]
    full_budget = sum(budget.values(), Decimal(0)) or None
    outlook = fetch_one(f"""SELECT SUM(latest_outlook) AS o, SUM(remaining_budget_gap) AS g
                            FROM v_outlook_quarter
                            WHERE financial_year = %(fy)s{where}""", params)

    # Where the difference is coming from.
    by_manager = fetch_all("""
        WITH now AS (SELECT canonical_manager, SUM(net_actual_income) AS v
                     FROM v_actual_month WHERE financial_year = %(fy)s GROUP BY 1),
             was AS (SELECT canonical_manager, SUM(net_actual_income) AS v
                     FROM v_actual_month
                     WHERE financial_year = %(py)s
                       AND period_month < (DATE %(py_cut)s + INTERVAL '1 month')
                     GROUP BY 1)
        SELECT COALESCE(now.canonical_manager, was.canonical_manager) AS canonical_manager,
               COALESCE(now.v, 0) AS this_year,
               COALESCE(was.v, 0) AS prior_year,
               COALESCE(now.v, 0) - COALESCE(was.v, 0) AS change
        FROM now FULL OUTER JOIN was ON was.canonical_manager = now.canonical_manager
        ORDER BY change DESC""",
        {"fy": financial_year, "py": financial_year - 1,
         "py_cut": dt.date(cut.year - 1, cut.month, 1)})

    by_type = fetch_all("""
        WITH now AS (SELECT business_classification, SUM(actual_income) AS v
                     FROM v_sales_reported WHERE financial_year = %(fy)s GROUP BY 1),
             was AS (SELECT business_classification, SUM(actual_income) AS v
                     FROM v_sales_reported
                     WHERE financial_year = %(py)s
                       AND period_month < (DATE %(py_cut)s + INTERVAL '1 month')
                     GROUP BY 1)
        SELECT COALESCE(now.business_classification, was.business_classification)
                   AS classification,
               COALESCE(now.v, 0) AS this_year,
               COALESCE(was.v, 0) AS prior_year,
               COALESCE(now.v, 0) - COALESCE(was.v, 0) AS change
        FROM now FULL OUTER JOIN was
          ON was.business_classification = now.business_classification
        ORDER BY change DESC""",
        {"fy": financial_year, "py": financial_year - 1,
         "py_cut": dt.date(cut.year - 1, cut.month, 1)})

    return YearOverYear(
        financial_year=financial_year,
        label=f"FY{financial_year}-{str(financial_year + 1)[2:]}",
        prior_label=f"FY{financial_year - 1}-{str(financial_year)[2:]}",
        months=points,
        ytd_actual=Money.of(ytd_actual, FUTURE),
        ytd_prior_year=Money.of(ytd_prior, NO_PRIOR),
        ytd_growth=Money.of(growth, NO_PRIOR),
        ytd_growth_pct=Ratio.of(_pct_change(ytd_actual, ytd_prior), NO_PRIOR),
        ytd_budget=Money.of(ytd_budget),
        ytd_variance=Money.of(variance),
        ytd_achievement=Ratio.of(achievement),
        on_track=on_track, verdict=verdict,
        prior_year_full=Money.of(prior_full, NO_PRIOR),
        full_year_budget=Money.of(full_budget),
        latest_outlook=Money.of(outlook["o"] if outlook else None),
        remaining_gap=Money.of(outlook["g"] if outlook else None),
        outlook_vs_prior_year_pct=Ratio.of(
            _pct_change(outlook["o"] if outlook else None, prior_full), NO_PRIOR),
        growth_by_manager=by_manager, growth_by_type=by_type,
        meta=meta(financial_year, notes=[
            f"Prior-year comparison is like-for-like: FY{financial_year - 1}-"
            f"{str(financial_year)[2:]} is cut at the same month of the year as the "
            "current reporting cut-off, so a part year is never compared with a "
            "full one."]))


# --- all managers, month by month ---------------------------------------------

@router.get("/analytics/manager-matrix", tags=["analytics"])
def manager_matrix(financial_year: int = Query(2026),
                   measure: str = Query("net_actual",
                                        pattern="^(net_actual|budget|variance|"
                                                "achievement|original_forecast)$"),
                   include_non_ranked: bool = Query(False),
                   user=Depends(current_user)):
    """Every manager down the side, every month across the top.

    One measure at a time, because a matrix showing several measures at once is
    unreadable and invites mis-reading one for another.
    """
    cut = _cut_month()
    months = _fy_months(financial_year)
    params = {"fy": financial_year}

    actual = {(r["canonical_manager"], r["period_month"]): r["v"] for r in fetch_all("""
        SELECT canonical_manager, period_month, SUM(net_actual_income) AS v
        FROM v_actual_month WHERE financial_year = %(fy)s GROUP BY 1, 2""", params)}
    budget = {(r["canonical_manager"], r["forecast_month"]): r["v"] for r in fetch_all("""
        SELECT canonical_manager, forecast_month, SUM(total_budget) AS v
        FROM v_monthly_budget WHERE financial_year = %(fy)s GROUP BY 1, 2""", params)}
    original = {(r["canonical_manager"], r["forecast_month"]): r["v"] for r in fetch_all("""
        SELECT canonical_manager, forecast_month, SUM(original_forecast) AS v
        FROM v_forecast_position_month WHERE financial_year = %(fy)s
        GROUP BY 1, 2""", params)}

    # Every manager, always. include_non_ranked decides whether a non-ranked
    # manager is LISTED, never whether their income is COUNTED.
    #
    # This used to filter them out of the query, so they vanished from the rows,
    # the column totals and the grand total together. FY2024 came back at
    # $754,812.92 against $756,700.56 in the view -- short by $1,887.64, which
    # was one manager's entire year. A total that quietly omits somebody is worse
    # than one that shows them awkwardly: it cannot be reconciled against any
    # other figure in the system, and nothing on screen says a name is missing.
    managers = fetch_all("""SELECT canonical_manager, status, include_in_rankings
                            FROM reporting_manager
                            ORDER BY display_order NULLS LAST, canonical_manager""")

    rows = []
    for m in managers:
        name = m["canonical_manager"]
        cells, total = [], Decimal(0)
        has_any = False
        for month in months:
            started = month <= cut
            a, b, o = (actual.get((name, month)), budget.get((name, month)),
                       original.get((name, month)))
            if measure == "net_actual":
                # A started month with no figure is unavailable, not "actual".
                # Labelling a null as actual claims a real number that is not
                # there, and the grid cannot then tell $0.00 from N/A -- which is
                # the distinction the whole display convention rests on.
                if not started:
                    v, status = None, "future"
                elif a is None:
                    v, status = None, "unavailable"
                else:
                    v, status = a, "actual"
            elif measure == "budget":
                v, status = b, "actual" if b is not None else "unavailable"
            elif measure == "original_forecast":
                v, status = o, "actual" if o is not None else "unavailable"
            elif measure == "variance":
                v = (a - b) if (started and a is not None and b is not None) else None
                status = "actual" if v is not None else ("future" if not started
                                                         else "unavailable")
            else:
                v = (a / b) if (started and a is not None and b) else None
                status = "actual" if v is not None else ("future" if not started
                                                         else "unavailable")
            if v is not None and measure != "achievement":
                total += v
                has_any = True
            cells.append({"month": month, "value": v, "status": status})
        rows.append({"canonical_manager": name, "status": m["status"],
                     "include_in_rankings": m["include_in_rankings"],
                     "cells": cells,
                     "total": total if has_any and measure != "achievement" else None})

    # Every manager is returned, each carrying include_in_rankings, and the
    # caller decides what to rank. Filtering here made the listed rows stop
    # summing to the grand total -- a grid whose own figures do not add up to its
    # own total is worse than one showing an extra name, and it is the same fault
    # in a new place: someone counted but not visible.
    excluded_from_listing: list[str] = []

    column_totals = []
    for i, month in enumerate(months):
        vals = [r["cells"][i]["value"] for r in rows if r["cells"][i]["value"] is not None]
        column_totals.append({
            "month": month,
            "value": sum(vals) if vals and measure != "achievement" else None,
            "status": "actual" if vals else ("future" if month > cut else "unavailable"),
        })

    return {"financial_year": financial_year, "measure": measure,
            "months": months,
            "month_status": ["completed" if m <= cut else "future" for m in months],
            "rows": rows, "column_totals": column_totals,
            # Everyone, listed or not, so this reconciles against the views.
            "grand_total": (sum(r["total"] for r in rows if r["total"] is not None)
                            if measure != "achievement" else None),
            "totals_include_non_ranked": True,
            "non_ranked_managers": [r["canonical_manager"] for r in rows
                                   if not r["include_in_rankings"]],
            "meta": meta(financial_year), "gst_note": GST_NOTE}


# --- return income, simplified -------------------------------------------------

@router.get("/analytics/return-income", tags=["analytics"])
def return_income_analysis(financial_year: int | None = None,
                           manager: str | None = None, user=Depends(current_user)):
    """Return income by category, as a share of the income it eats into.

    The signed and absolute columns of the earlier version carried the same
    information twice. What matters is how large each category is and what
    proportion of positive income it removes.
    """
    clauses, params = [], {}
    if financial_year:
        clauses.append("financial_year = %(fy)s")
        params["fy"] = financial_year
    if manager:
        clauses.append("canonical_manager = %(mgr)s")
        params["mgr"] = manager
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""

    rows = fetch_all(f"""
        SELECT derived_classification AS classification,
               SUM(absolute_return_income) AS amount,
               SUM(transaction_rows) AS transactions
        FROM v_return_income_analysis{where}
        GROUP BY 1 ORDER BY 2 DESC""", params)

    pos_where = where.replace("financial_year", "financial_year") or ""
    positive = fetch_one(f"""
        SELECT COALESCE(SUM(positive_actual_income), 0) AS positive,
               COALESCE(SUM(net_actual_income), 0) AS net
        FROM v_actual_month{pos_where}""", params)

    total = sum((r["amount"] for r in rows), Decimal(0))
    items = []
    for r in rows:
        items.append({
            "classification": r["classification"],
            "amount": r["amount"],
            "transactions": r["transactions"],
            "share_of_returns": (r["amount"] / total) if total else None,
            "share_of_positive_income": (r["amount"] / positive["positive"])
                                        if positive["positive"] else None,
            "average_per_transaction": (r["amount"] / r["transactions"])
                                       if r["transactions"] else None,
        })

    return {"items": items,
            "total_return_income": total,
            "positive_income": positive["positive"],
            "net_income": positive["net"],
            "return_rate": (total / positive["positive"]) if positive["positive"] else None,
            "meta": meta(financial_year, notes=[
                "Return income is shown as a positive amount: it is money that came "
                "back out. It reduces Net Actual Income, which is why Net is lower "
                "than Positive."]),
            "gst_note": GST_NOTE}
