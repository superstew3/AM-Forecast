"""Reporting endpoints.

Every figure below is selected from a view. Nothing is recomputed here.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .core import (
    current_month, last_completed_month,
    supplied_month_note,
    current_financial_year,
    GST_NOTE, Filters, Meta, Money, Page, Ratio, columns_of, current_user,
    fetch_all, fetch_one, filters, meta, paginate,
)

router = APIRouter()

# Reasons attached to unavailable measures, so the interface can explain rather
# than just blank the cell.
NO_BASELINE = ("No renewal forecast recorded for this period, so achievement "
               "cannot be calculated.")
NO_LATEST = ("This month is complete and reports actuals. A completed month has "
             "no Latest Forecast.")
NO_BUDGET = ("No budget applies: this manager has no Original Renewal Forecast "
             "for the period.")
ZERO_DENOM = "Denominator is zero, so a percentage cannot be calculated."


# --- overall business ---------------------------------------------------------

class BusinessSummary(BaseModel):
    financial_year: int
    coverage_status: str | None
    period_label: str | None
    net_actual_income: Money
    positive_actual_income: Money
    return_income: Money
    original_renewal_forecast: Money
    latest_renewal_forecast: Money
    forecast_movement: Money
    total_budget: Money
    budget_achievement: Ratio
    latest_outlook: Money
    remaining_budget_gap: Money
    actual_new_business: Money
    lapse_return_income: Money
    midterm_cancellation_return_income: Money
    new_business_cancellation_return_income: Money
    negative_endorsements: Money
    endorsement_cancellations: Money
    meta: Meta


@router.get("/business", response_model=BusinessSummary, tags=["business"])
def business(financial_year: int | None = Query(None), user=Depends(current_user)):
    financial_year = financial_year or current_financial_year()
    row = fetch_one("""SELECT * FROM v_business_dashboard WHERE financial_year=%(fy)s""",
                    {"fy": financial_year})
    if row is None:
        raise HTTPException(404, f"no data for financial year {financial_year}")
    notes = []
    if row["coverage_status"] == "partial":
        notes.append(row["period_label"] or "Partial period: not a full financial year.")
    notes.extend(supplied_month_note(financial_year))
    return BusinessSummary(
        financial_year=row["financial_year"],
        coverage_status=row["coverage_status"], period_label=row["period_label"],
        net_actual_income=Money.of(row["net_actual_income"]),
        positive_actual_income=Money.of(row["positive_actual_income"]),
        return_income=Money.of(row["return_income"]),
        original_renewal_forecast=Money.of(row["original_renewal_forecast"]),
        latest_renewal_forecast=Money.of(row["latest_renewal_forecast"], NO_LATEST),
        forecast_movement=Money.of(row["forecast_movement"], NO_LATEST),
        total_budget=Money.of(row["total_budget"], NO_BUDGET),
        budget_achievement=Ratio.of(row["budget_achievement"], NO_BASELINE),
        latest_outlook=Money.of(row["latest_outlook"]),
        remaining_budget_gap=Money.of(row["remaining_budget_gap"], NO_BUDGET),
        actual_new_business=Money.of(row["actual_new_business"]),
        lapse_return_income=Money.of(row["lapse_income_returned"]),
        midterm_cancellation_return_income=Money.of(row["midterm_cancellation_returned"]),
        new_business_cancellation_return_income=Money.of(row["new_business_cancellation"]),
        negative_endorsements=Money.of(row["negative_endorsements"]),
        endorsement_cancellations=Money.of(row["endorsement_cancellations"]),
        meta=meta(financial_year, notes))


# --- account managers ---------------------------------------------------------

class ManagerRow(BaseModel):
    canonical_manager: str
    status: str
    include_in_rankings: bool
    period: str
    financial_year: int
    financial_quarter: int | None = None
    period_month: dt.date | None = None
    original_forecast: Money
    latest_forecast: Money
    positive_actual_income: Money
    return_income: Money
    net_actual_income: Money
    new_business_growth_target: Money
    total_budget: Money
    budget_variance: Money
    budget_achievement: Ratio
    renewal_achievement: Ratio
    actual_new_business: Money
    latest_outlook: Money
    remaining_budget_gap: Money
    renewal_income: Money
    renewal_forecast: Money
    budget_to_date: Money
    months_elapsed: int = 0
    budget_verdict: str
    over_or_under_pct: Ratio
    baseline_note: str | None = None
    # A period that has not started is not "unavailable" — it simply has not
    # happened. The interface renders an em dash for these, not N/A.
    has_started: bool = True


class ManagerResponse(BaseModel):
    items: list[ManagerRow]
    total: int
    meta: Meta


_MANAGER_SQL = """
-- The month boundary comes from the calendar, not the stored cut-off.
--
-- Migration 0020 moved every VIEW onto reporting_current_month(). These SQL
-- fragments and Python filters in the API were missed, so the two disagreed: the
-- views knew August had started and the endpoints did not.
--
-- The effect was that an accepted sales file containing August simply did not
-- appear. The import worked, the rows were in sales_transaction, and every
-- reporting surface dropped them because period_month > cut_month. Nothing
-- errored, and nothing said why.
WITH cut AS (SELECT reporting_current_month() AS cut_month),
act AS (
    SELECT canonical_manager, financial_year, financial_quarter, period_month,
           positive_actual_income, absolute_return_income, net_actual_income,
           actual_new_business
    FROM v_actual_month
),
fcst AS (
    SELECT canonical_manager, financial_year, financial_quarter, forecast_month,
           original_forecast, latest_forecast
    FROM v_forecast_position_month
),
bud AS (
    SELECT canonical_manager, financial_year, financial_quarter, forecast_month,
           new_business_growth_target, total_budget
    FROM v_monthly_budget
),
perf AS (
    -- Manager-month renewal achievement. Does not depend on policy-level
    -- matching, so it works from the first upload.
    SELECT canonical_manager, period_month AS forecast_month, renewal_achievement,
           renewal_income, original_forecast AS renewal_forecast
    FROM v_renewal_income_month
),
base AS (
    SELECT COALESCE(a.canonical_manager, f.canonical_manager, b.canonical_manager)
               AS canonical_manager,
           COALESCE(a.financial_year, f.financial_year, b.financial_year) AS financial_year,
           COALESCE(a.financial_quarter, f.financial_quarter, b.financial_quarter)
               AS financial_quarter,
           COALESCE(a.period_month, f.forecast_month, b.forecast_month) AS period_month,
           f.original_forecast, f.latest_forecast,
           a.positive_actual_income, a.absolute_return_income, a.net_actual_income,
           a.actual_new_business,
           b.new_business_growth_target, b.total_budget,
           p.renewal_achievement, p.renewal_income, p.renewal_forecast
    FROM act a
    FULL OUTER JOIN fcst f ON f.canonical_manager = a.canonical_manager
                          AND f.forecast_month = a.period_month
    FULL OUTER JOIN bud b ON b.canonical_manager = COALESCE(a.canonical_manager,
                                                            f.canonical_manager)
                         AND b.forecast_month = COALESCE(a.period_month, f.forecast_month)
    LEFT JOIN perf p ON p.canonical_manager = COALESCE(a.canonical_manager,
                                                       f.canonical_manager)
                    AND p.forecast_month = COALESCE(a.period_month, f.forecast_month)
)
SELECT base.*, m.status, m.include_in_rankings,
       bu.baseline_usable, fb.baseline_source, fb.note AS baseline_note
FROM base
JOIN reporting_manager m ON m.canonical_manager = base.canonical_manager
LEFT JOIN v_baseline_usable bu ON bu.canonical_manager = base.canonical_manager
                              AND bu.forecast_month = base.period_month
LEFT JOIN forecast_baseline fb ON fb.forecast_month = base.period_month
"""


def _aggregate(rows: list[dict], period: str, completed_month,
               started_month) -> list[dict]:
    """Roll monthly rows up to quarter, year-to-date or full year.

    Sums are over NULL-tolerant addition: a NULL component leaves the total NULL
    only where every contributing period is NULL, which keeps N/A meaningful
    rather than contagious.
    """
    keys: dict[tuple, dict] = {}
    for r in rows:
        if period == "month":
            key = (r["canonical_manager"], r["financial_year"], r["period_month"])
        elif period == "quarter":
            key = (r["canonical_manager"], r["financial_year"], r["financial_quarter"])
        else:
            key = (r["canonical_manager"], r["financial_year"])
        acc = keys.setdefault(key, {
            "canonical_manager": r["canonical_manager"],
            "financial_year": r["financial_year"],
            "financial_quarter": r["financial_quarter"] if period != "year" else None,
            "period_month": r["period_month"] if period == "month" else None,
            "status": r["status"], "include_in_rankings": r["include_in_rankings"],
            "baseline_note": r.get("baseline_note"),
            "_any_baseline": False, "_all_baseline": True,
        })
        # Forecast and budget cover the WHOLE period, including months still to
        # come -- a full-year budget is a full year. Actual income covers only
        # months that have STARTED, because a month that has not begun cannot
        # have produced any.
        #
        # Summing both the same way let a quarter labelled "not started" report
        # income anyway. The label and the figure were computed by different
        # logic, so nothing made them agree, and one row could assert both at
        # once. In production the sums happened to match because a future month
        # holds no transactions -- the contradiction only became visible against
        # a dataset with data beyond the boundary, which is precisely the case
        # nobody would think to check.
        started_here = bool(r["period_month"]) and r["period_month"] <= started_month
        for field in ("original_forecast", "latest_forecast",
                      "new_business_growth_target", "total_budget"):
            v = r.get(field)
            if v is not None:
                acc[field] = (acc.get(field) or 0) + v
            else:
                acc.setdefault(field, None)
        for field in ("positive_actual_income", "absolute_return_income",
                      "net_actual_income", "actual_new_business"):
            v = r.get(field) if started_here else None
            if v is not None:
                acc[field] = (acc.get(field) or 0) + v
            else:
                acc.setdefault(field, None)

        # Budget and forecast for the months that have actually happened.
        # A quarter one month in must be measured against one month of budget,
        # not three. Comparing July actuals with a whole-quarter budget reported
        # every manager at roughly a third of target, which is arithmetic, not
        # performance.
        # COMPLETED months only for anything that gets judged.
        #
        # budget_to_date, forecast_to_date and months_elapsed are the
        # denominators of achievement. Counting the month under way among them
        # puts a part month's income against a whole month's target -- which on
        # the bonus tracker reported nearly every manager as well behind while
        # three of them were ahead, and is fixed there by migration 0022.
        #
        # Income itself is NOT filtered here: it is shown for the month under
        # way, because it has been earned. Shown, not scored.
        if r["period_month"] and r["period_month"] <= completed_month:
            for field, dest in (("total_budget", "budget_to_date"),
                                ("original_forecast", "forecast_to_date")):
                v = r.get(field)
                if v is not None:
                    acc[dest] = (acc.get(dest) or 0) + v
            acc["months_elapsed"] = acc.get("months_elapsed", 0) + 1
        acc.setdefault("budget_to_date", None)
        acc.setdefault("forecast_to_date", None)
        acc.setdefault("months_elapsed", 0)
        usable = bool(r.get("baseline_usable"))
        acc["_any_baseline"] = acc["_any_baseline"] or usable
        acc["_all_baseline"] = acc["_all_baseline"] and usable
    return list(keys.values())


@router.get("/managers", response_model=ManagerResponse, tags=["managers"])
def managers(period: str = Query("quarter", pattern="^(month|quarter|ytd|year)$"),
             financial_year: int | None = Query(None),
             include_non_ranked: bool = Query(False),
             f: Filters = Depends(filters), user=Depends(current_user)):
    """Manager performance at month, quarter, year-to-date or full-year grain.

    Inactive, legacy and unmapped managers are out of rankings by default. Their
    actual income still counts towards business totals: those are different
    questions and the flags are separate.
    """
    financial_year = financial_year or current_financial_year()
    params = {"fy": financial_year}
    sql = _MANAGER_SQL + " WHERE base.financial_year = %(fy)s"
    if f.manager:
        sql += " AND base.canonical_manager = %(manager)s"
        params["manager"] = f.manager
    if f.quarter:
        sql += " AND base.financial_quarter = %(quarter)s"
        params["quarter"] = f.quarter
    # A ranking must not list somebody excluded from rankings, so this filter
    # stays. The fault was never here -- it was that the business totals were
    # built from the same filtered set, so a manager kept out of a leaderboard
    # also vanished from the income. Rankings and totals answer different
    # questions and are now computed from different sets.
    if not include_non_ranked:
        sql += " AND m.include_in_rankings"
    rows = fetch_all(sql, params)

    # THE one that hid an accepted upload.
    #
    # This drops every row after the boundary, so with the stored cut-off at
    # 31 July an August sales file was imported, accepted, and then filtered out
    # of the account-manager figures entirely. The rows were in the database the
    # whole time. Nothing errored; the numbers simply did not move.
    #
    # The calendar knows August has started. The setting did not, and nobody had
    # any reason to think a reporting setting still governed whether an import
    # showed up.
    # Two boundaries, deliberately.
    #
    # Year to date INCLUDES the month under way: its income has been earned and
    # belongs on the page the day it is imported. Filtering it out is what made
    # an accepted August upload invisible.
    #
    # Budget and forecast to date exclude it, because those are what income is
    # measured against, and a whole month's target against a part month's income
    # is not a result. Same rule as migration 0022 on the bonus tracker.
    cut = current_month()
    completed = last_completed_month()
    if period == "ytd":
        rows = [r for r in rows if r["period_month"] and r["period_month"] <= cut]

    grain = "month" if period == "month" else ("quarter" if period == "quarter" else "year")
    aggregated = _aggregate(rows, grain, completed, cut)

    outlook = {(r["canonical_manager"], r["financial_quarter"]): r
               for r in fetch_all("""SELECT canonical_manager, financial_quarter,
                                            latest_outlook, remaining_budget_gap
                                     FROM v_outlook_quarter
                                     WHERE financial_year = %(fy)s""", params)}
    # Renewal achievement is aggregated over the same grain as the row, so a
    # quarterly row compares quarterly renewal income with quarterly forecast
    # rather than borrowing a single month's ratio.
    grain_cols = {"month": "period_month",
                  "quarter": "financial_quarter",
                  "ytd": "financial_year",
                  "year": "financial_year"}[period]
    renewal_rows = fetch_all(f"""
        SELECT canonical_manager, {grain_cols} AS bucket,
               SUM(renewal_income) AS renewal_income,
               SUM(original_forecast) AS renewal_forecast,
               safe_div(SUM(renewal_income), SUM(original_forecast)) AS renewal_achievement,
               bool_or(period_started) AS started
        FROM v_renewal_income_month
        WHERE financial_year = %(fy)s AND period_started
        GROUP BY 1, 2""", {"fy": financial_year})
    perf = {(r["canonical_manager"], r["bucket"]): r for r in renewal_rows}

    def started(row) -> bool:
        if period == "month":
            return bool(row["period_month"]) and row["period_month"] <= cut
        if period == "quarter":
            months = [m for m in (r["period_month"] for r in rows)
                      if m and r_quarter(m) == row["financial_quarter"]]
            return any(m <= cut for m in months) if months else False
        return True

    def r_quarter(m):
        return ((m.month - 7) % 12) // 3 + 1

    items = []
    for a in sorted(aggregated,
                    key=lambda x: (not started(x), -(x.get("total_budget") or 0))):
        o = outlook.get((a["canonical_manager"], a["financial_quarter"]), {})
        bucket = (a["period_month"] if period == "month"
                  else a["financial_quarter"] if period == "quarter"
                  else a["financial_year"])
        p = perf.get((a["canonical_manager"], bucket), {})
        budget = a.get("total_budget")
        # Measured against the budget for the months elapsed, not the whole period.
        budget_measured = a.get("budget_to_date")
        net = a.get("net_actual_income")
        baseline_ok = a["_any_baseline"]
        variance = (net - budget_measured) if (budget_measured is not None
                                               and net is not None
                                               and baseline_ok) else None
        achievement = (net / budget_measured) if (budget_measured not in (None, 0)
                                                  and net is not None
                                                  and baseline_ok) else None
        items.append(ManagerRow(
            canonical_manager=a["canonical_manager"], status=a["status"],
            include_in_rankings=a["include_in_rankings"],
            period=period, financial_year=a["financial_year"],
            financial_quarter=a["financial_quarter"], period_month=a["period_month"],
            original_forecast=Money.of(a.get("original_forecast"), NO_BASELINE),
            latest_forecast=Money.of(a.get("latest_forecast"), NO_LATEST),
            positive_actual_income=Money.of(a.get("positive_actual_income")),
            return_income=Money.of(a.get("absolute_return_income")),
            net_actual_income=Money.of(net),
            new_business_growth_target=Money.of(a.get("new_business_growth_target"),
                                                NO_BUDGET),
            total_budget=Money.of(budget, NO_BUDGET),
            budget_variance=Money.of(variance,
                                     NO_BASELINE if not baseline_ok else NO_BUDGET),
            budget_achievement=Ratio.of(achievement,
                                        NO_BASELINE if not baseline_ok else ZERO_DENOM),
            renewal_achievement=Ratio.of(p.get("renewal_achievement"), NO_BASELINE),
            actual_new_business=Money.of(a.get("actual_new_business")),
            latest_outlook=Money.of(o.get("latest_outlook")),
            remaining_budget_gap=Money.of(o.get("remaining_budget_gap"), NO_BUDGET),
            renewal_income=Money.of(p.get("renewal_income")),
            renewal_forecast=Money.of(p.get("renewal_forecast"), NO_BASELINE),
            budget_to_date=Money.of(budget_measured, NO_BUDGET),
            months_elapsed=a.get("months_elapsed", 0),
            budget_verdict=(
                "Not measurable" if achievement is None
                else "Made budget" if achievement >= 1 else "Below budget"),
            over_or_under_pct=Ratio.of(
                (achievement - 1) if achievement is not None else None, NO_BUDGET),
            baseline_note=a.get("baseline_note"),
            has_started=started(a)))
    return ManagerResponse(items=items, total=len(items), meta=meta(financial_year))


# --- forecast movement --------------------------------------------------------

@router.get("/forecast-movement", tags=["forecast"])
def forecast_movement(f: Filters = Depends(filters), limit: int = 200, offset: int = 0,
                      user=Depends(current_user)):
    """Original to Latest movement.

    Manager transfers are counted from the independent `manager_changed` flag,
    so a policy that moved manager *and* changed amount is counted as both.
    """
    where, params = "", {}
    if f.manager:
        where, params = " WHERE canonical_manager = %(manager)s", {"manager": f.manager}
    summary = fetch_all(f"""
        SELECT forecast_month, canonical_manager,
               original_expected_income, policies_removed, expected_income_removed,
               policies_added, expected_income_added, amount_changes,
               policies_amount_changed, manager_transfers, detail_changes,
               multi_attribute_changes, latest_expected_income
        FROM v_forecast_movement_summary{where}
        ORDER BY forecast_month, canonical_manager""", params)
    totals = fetch_one(f"""
        SELECT COALESCE(SUM(policies_removed),0) AS policies_removed,
               COALESCE(SUM(expected_income_removed),0) AS income_removed,
               COALESCE(SUM(policies_added),0) AS policies_added,
               COALESCE(SUM(expected_income_added),0) AS income_added,
               COALESCE(SUM(amount_changes),0) AS amount_changes,
               COALESCE(SUM(manager_transfers),0) AS manager_transfers,
               COALESCE(SUM(detail_changes),0) AS detail_changes,
               COALESCE(SUM(multi_attribute_changes),0) AS multi_attribute_changes,
               COALESCE(SUM(expected_income_added),0)
                 - COALESCE(SUM(expected_income_removed),0)
                 + COALESCE(SUM(amount_changes),0) AS net_forecast_movement
        FROM v_forecast_movement_summary{where}""", params)
    detail, total = paginate("""
        SELECT policy_id, forecast_month, movement_type, secondary_changes,
               added, removed, amount_changed, manager_changed, detail_changed,
               original_income, previous_income, latest_income, movement_amount,
               canonical_from_manager, canonical_to_manager,
               client_code, policy_number, class_abbrev, expiry_date
        FROM v_forecast_movement_detail
        ORDER BY abs(movement_amount) DESC, policy_id""", {}, limit, offset)
    return {"summary": summary, "totals": totals,
            "detail": {"items": detail, "total": total, "limit": limit, "offset": offset},
            "meta": meta(notes=[
                "Manager transfers are counted by the independent manager-change "
                "flag, including policies that also changed amount."]),
            "gst_note": GST_NOTE}


# --- return income ------------------------------------------------------------

@router.get("/return-income", tags=["returns"])
def return_income(f: Filters = Depends(filters), user=Depends(current_user)):
    # This endpoint takes its year from Filters rather than its own argument, so
    # it needs the default here or it would query every year at once and report a
    # total nobody asked for.
    if f.financial_year is None:
        f.financial_year = current_financial_year()
    where, params = f.clauses(columns_of("v_return_income_analysis"))
    rows = fetch_all(f"""
        SELECT derived_classification,
               SUM(signed_return_income) AS signed_return_income,
               SUM(absolute_return_income) AS absolute_return_income,
               SUM(transaction_rows) AS transaction_rows
        FROM v_return_income_analysis{where}
        GROUP BY 1 ORDER BY 3 DESC""", params)
    total = fetch_one(f"""
        SELECT COALESCE(SUM(signed_return_income),0) AS signed,
               COALESCE(SUM(absolute_return_income),0) AS absolute,
               COALESCE(SUM(transaction_rows),0) AS rows
        FROM v_return_income_analysis{where}""", params)

    # By manager as well as by class. Return income was only ever grouped by
    # classification, which answers "what is being returned" but not "whose book
    # it is coming out of" -- and the second question is the one that leads
    # somewhere, because a concentration in one book is a pattern worth asking
    # about rather than a number to note.
    by_manager = fetch_all(f"""
        SELECT canonical_manager,
               SUM(signed_return_income)   AS signed_return_income,
               SUM(absolute_return_income) AS absolute_return_income,
               SUM(transaction_rows)       AS transaction_rows
        FROM v_return_income_analysis{where}
        GROUP BY 1 ORDER BY SUM(absolute_return_income) DESC NULLS LAST""", params)

    months = [r["period_month"] for r in fetch_all("""
        SELECT DISTINCT period_month FROM v_return_income_analysis
        WHERE financial_year = %(fy)s AND period_month IS NOT NULL
        ORDER BY period_month""", {"fy": f.financial_year})]

    return {"items": rows, "by_manager": by_manager, "months": months,
            "total": total, "meta": meta(f.financial_year),
            "gst_note": GST_NOTE}


# --- new business -------------------------------------------------------------

@router.get("/new-business", tags=["new-business"])
def new_business(financial_year: int | None = Query(None), f: Filters = Depends(filters),
                 user=Depends(current_user)):
    financial_year = financial_year or current_financial_year()
    params = {"fy": financial_year}
    clause = ""
    if f.manager:
        clause = " AND canonical_manager = %(manager)s"
        params["manager"] = f.manager
    # The period narrows the same rows rather than selecting a different view,
    # so a month, a quarter and the year to date are the same figures at
    # different grain and cannot disagree with each other.
    if f.quarter:
        clause += " AND financial_quarter = %(quarter)s"
        params["quarter"] = f.quarter
    if f.month:
        clause += " AND period_month = %(month)s"
        params["month"] = f.month

    rows = fetch_all(f"""
        SELECT canonical_manager,
               SUM(new_business_count)                AS new_business_count,
               SUM(correction_count)                  AS correction_count,
               SUM(cancellation_count)                AS cancellation_count,
               SUM(transaction_count)                 AS transaction_count,
               SUM(gross_new_business)                AS gross_new_business,
               SUM(negative_new_business_corrections) AS negative_new_business_corrections,
               SUM(new_business_cancellations)        AS new_business_cancellations,
               SUM(net_new_business)                  AS net_new_business
        FROM v_new_business_analysis
        WHERE financial_year = %(fy)s{clause}
        GROUP BY canonical_manager
        ORDER BY SUM(net_new_business) DESC NULLS LAST""", params)

    # Months present in the year, so the interface can offer only periods that
    # exist rather than a fixed twelve with most of them empty.
    months = [r["period_month"] for r in fetch_all("""
        SELECT DISTINCT period_month FROM v_new_business_analysis
        WHERE financial_year = %(fy)s ORDER BY period_month""",
        {"fy": financial_year})]

    return {
        "items": rows,
        "months": months,
        "meta": meta(financial_year, notes=[
            "Future new business is never forecast. New business is recognised only "
            "when it appears in Sales Transactions, so it is absent from Latest "
            "Forecast and Latest Outlook."]),
        "gst_note": GST_NOTE}


# --- policy-level renewals ----------------------------------------------------

@router.get("/policies", tags=["policies"])
def policies(f: Filters = Depends(filters), limit: int = Query(100, le=1000),
             offset: int = 0, outcome: str | None = None,
             user=Depends(current_user)):
    parts, params = [], {}
    if f.manager:
        parts.append("canonical_manager = %(manager)s")
        params["manager"] = f.manager
    if f.client:
        parts.append("upper(client_code) LIKE %(client)s")
        params["client"] = f"%{f.client.upper()}%"
    if f.policy_number:
        parts.append("upper(policy_number) LIKE %(policy_number)s")
        params["policy_number"] = f"%{f.policy_number.upper()}%"
    if f.policy_class:
        parts.append("upper(class_abbrev) = upper(%(policy_class)s)")
        params["policy_class"] = f.policy_class
    if f.underwriter:
        parts.append("upper(underwriter_abbrev) = upper(%(underwriter)s)")
        params["underwriter"] = f.underwriter
    if f.month:
        parts.append("forecast_month = %(month)s")
        params["month"] = f.month
    if outcome:
        parts.append("outcome = %(outcome)s")
        params["outcome"] = outcome
    where = (" WHERE " + " AND ".join(parts)) if parts else ""
    rows, total = paginate(f"""
        SELECT policy_id, forecast_month, client_code, policy_number, class_abbrev,
               underwriter_abbrev, expiry_date, original_manager, canonical_manager,
               original_forecast_income, latest_forecast_income, forecast_movement,
               renewal_transaction_income, total_associated_income, outcome,
               best_tier, confidence, requires_review, is_manual, exception_flags,
               source_snapshot, matched_transaction_count
        FROM v_policy_renewal{where}
        ORDER BY original_forecast_income DESC NULLS LAST, policy_id""",
        params, limit, offset)
    return {"items": rows, "total": total, "limit": limit, "offset": offset,
            "meta": meta(notes=[
                "Renewal income is RWL and TRW only, plus corrections linked to the "
                "renewal by invoice chain. Total associated income includes every "
                "line attached to the policy and answers a different question."]),
            "gst_note": GST_NOTE}
