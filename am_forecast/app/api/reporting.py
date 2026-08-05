"""Reporting endpoints.

Every figure below is selected from a view. Nothing is recomputed here.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .core import (
    GST_NOTE, Filters, Meta, Money, Page, Ratio, columns_of, current_user,
    fetch_all, fetch_one, filters, meta, paginate,
)

router = APIRouter()

# Reasons attached to unavailable measures, so the interface can explain rather
# than just blank the cell.
NO_BASELINE = ("No usable Original Forecast baseline for this period, so "
               "achievement cannot be calculated.")
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
def business(financial_year: int = Query(2026), user=Depends(current_user)):
    row = fetch_one("""SELECT * FROM v_business_dashboard WHERE financial_year=%(fy)s""",
                    {"fy": financial_year})
    if row is None:
        raise HTTPException(404, f"no data for financial year {financial_year}")
    notes = []
    if row["coverage_status"] == "partial":
        notes.append(row["period_label"] or "Partial period: not a full financial year.")
    if financial_year == 2026:
        notes.append(
            "July 2026 has a legacy manager-month baseline, not policy-level detail. "
            "Policy-level renewal achievement is reliable from August 2026 onward.")
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
    retention_by_income: Ratio
    retention_by_policy_count: Ratio
    baseline_note: str | None = None


class ManagerResponse(BaseModel):
    items: list[ManagerRow]
    total: int
    meta: Meta


_MANAGER_SQL = """
WITH cut AS (SELECT date_trunc('month', cut_off_date)::date AS cut_month
             FROM reporting_settings WHERE id=1),
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
    SELECT canonical_manager, forecast_month, renewal_achievement,
           retention_by_income, retention_by_policy_count
    FROM v_renewal_outcome_performance
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
           p.renewal_achievement, p.retention_by_income, p.retention_by_policy_count
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


def _aggregate(rows: list[dict], period: str) -> list[dict]:
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
        for field in ("original_forecast", "latest_forecast", "positive_actual_income",
                      "absolute_return_income", "net_actual_income",
                      "actual_new_business", "new_business_growth_target",
                      "total_budget"):
            v = r.get(field)
            if v is not None:
                acc[field] = (acc.get(field) or 0) + v
            else:
                acc.setdefault(field, None)
        usable = bool(r.get("baseline_usable"))
        acc["_any_baseline"] = acc["_any_baseline"] or usable
        acc["_all_baseline"] = acc["_all_baseline"] and usable
    return list(keys.values())


@router.get("/managers", response_model=ManagerResponse, tags=["managers"])
def managers(period: str = Query("quarter", pattern="^(month|quarter|ytd|year)$"),
             financial_year: int = Query(2026),
             include_non_ranked: bool = Query(False),
             f: Filters = Depends(filters), user=Depends(current_user)):
    """Manager performance at month, quarter, year-to-date or full-year grain.

    Inactive, legacy and unmapped managers are out of rankings by default. Their
    actual income still counts towards business totals: those are different
    questions and the flags are separate.
    """
    params = {"fy": financial_year}
    sql = _MANAGER_SQL + " WHERE base.financial_year = %(fy)s"
    if f.manager:
        sql += " AND base.canonical_manager = %(manager)s"
        params["manager"] = f.manager
    if f.quarter:
        sql += " AND base.financial_quarter = %(quarter)s"
        params["quarter"] = f.quarter
    if not include_non_ranked:
        sql += " AND m.include_in_rankings"
    rows = fetch_all(sql, params)

    cut = fetch_one("""SELECT date_trunc('month', cut_off_date)::date AS m
                       FROM reporting_settings WHERE id=1""")["m"]
    if period == "ytd":
        rows = [r for r in rows if r["period_month"] and r["period_month"] <= cut]

    grain = "month" if period == "month" else ("quarter" if period == "quarter" else "year")
    aggregated = _aggregate(rows, grain)

    outlook = {(r["canonical_manager"], r["financial_quarter"]): r
               for r in fetch_all("""SELECT canonical_manager, financial_quarter,
                                            latest_outlook, remaining_budget_gap
                                     FROM v_outlook_quarter
                                     WHERE financial_year = %(fy)s""", params)}
    perf = {(r["canonical_manager"], r["forecast_month"]): r
            for r in fetch_all("""SELECT canonical_manager, forecast_month,
                                         renewal_achievement, retention_by_income,
                                         retention_by_policy_count
                                  FROM v_renewal_outcome_performance""")}

    items = []
    for a in sorted(aggregated, key=lambda x: -(x.get("total_budget") or 0)):
        o = outlook.get((a["canonical_manager"], a["financial_quarter"]), {})
        p = perf.get((a["canonical_manager"], a["period_month"]), {})
        budget = a.get("total_budget")
        net = a.get("net_actual_income")
        baseline_ok = a["_any_baseline"]
        variance = (net - budget) if (budget is not None and net is not None
                                      and baseline_ok) else None
        achievement = (net / budget) if (budget not in (None, 0) and net is not None
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
            retention_by_income=Ratio.of(p.get("retention_by_income"), NO_BASELINE),
            retention_by_policy_count=Ratio.of(p.get("retention_by_policy_count"),
                                               NO_BASELINE),
            baseline_note=a.get("baseline_note")))
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
    return {"items": rows, "total": total, "meta": meta(f.financial_year),
            "gst_note": GST_NOTE}


# --- new business -------------------------------------------------------------

@router.get("/new-business", tags=["new-business"])
def new_business(financial_year: int = Query(2026), f: Filters = Depends(filters),
                 user=Depends(current_user)):
    params = {"fy": financial_year}
    clause = ""
    if f.manager:
        clause = " AND canonical_manager = %(manager)s"
        params["manager"] = f.manager
    rows = fetch_all(f"""
        SELECT canonical_manager, financial_quarter,
               gross_new_business, negative_new_business_corrections,
               new_business_cancellations, net_new_business,
               new_business_growth_target, growth_target_achievement
        FROM v_new_business_analysis
        WHERE financial_year = %(fy)s{clause}
        ORDER BY financial_quarter, net_new_business DESC NULLS LAST""", params)
    return {
        "items": [{**r,
                   "growth_target_achievement": Ratio.of(r["growth_target_achievement"],
                                                         NO_BUDGET).model_dump()}
                  for r in rows],
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
