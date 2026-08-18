"""Per-manager detail.

Shaped after the AM sheets in the workbook this replaces: a transaction-type
grid by month, with forecast, budget and prior-year rows beneath it, and the
headline achievement figures at the top.

One presentational rule matters here. A month that has not happened yet is not
"unavailable" — it is simply future. Those are different things and the
interface must say so differently: a future month shows an em dash, an
unavailable measure shows N/A with a reason. Conflating them was what made the
first version of the manager screen look broken when it was merely early in the
year.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .core import GST_NOTE, Meta, Money, Ratio, current_user, fetch_all, fetch_one, meta

router = APIRouter()

# The order the workbook uses, so the page reads the way the business already
# thinks about these categories.
ROW_ORDER = [
    "Adjustment",
    "Endorsement",
    "Endorsement Cancellation",
    "Lapse / End-Term Lost Renewal",
    "Mid-Term Cancellation",
    "New Business",
    "New Business Cancellation",
    "Policy Reinstatement",
    "Renewal",
    "Transfer Renewal",
]

FUTURE = "Period has not started yet."
NO_BASELINE = "No renewal forecast recorded for this period."
NO_BUDGET = "No budget applies: no Original Renewal Forecast for this period."


class Cell(BaseModel):
    """One month of one measure."""

    month: dt.date
    value: Decimal | None = None
    status: str = "actual"        # actual | future | unavailable
    reason: str | None = None


class GridRow(BaseModel):
    label: str
    kind: str                      # transaction | total | forecast | budget | prior | derived
    # How to render the values. Declared by the API rather than guessed from the
    # label, so a renamed row cannot silently start formatting as currency.
    value_kind: str = "money"      # money | percent | count | verdict
    cells: list[Cell]
    total: Decimal | None = None
    hint: str | None = None


class ManagerDetail(BaseModel):
    canonical_manager: str
    status: str
    include_in_rankings: bool
    financial_year: int
    financial_year_label: str
    months: list[dt.date]
    month_status: list[str]
    cut_off_month: dt.date

    prior_year_actual: Money
    ytd_actual: Money
    ytd_budget: Money
    ytd_achievement: Ratio
    full_year_budget: Money
    full_year_original_forecast: Money
    full_year_latest_forecast: Money
    latest_outlook: Money
    remaining_budget_gap: Money
    forecast_achievement: Ratio
    active_growth_pct: Ratio
    active_growth_basis: str | None = None
    quarter_growth: list[dict] = []

    rows: list[GridRow]
    quarters: list[dict]
    meta: Meta


def australian_quarter(d: dt.date) -> int:
    """Q1 Jul-Sep, Q2 Oct-Dec, Q3 Jan-Mar, Q4 Apr-Jun."""
    return ((d.month - 7) % 12) // 3 + 1


def _fy_months(fy: int) -> list[dt.date]:
    out = []
    for i in range(12):
        month = 7 + i
        year = fy + (month - 1) // 12
        out.append(dt.date(year, (month - 1) % 12 + 1, 1))
    return out


def _cells(months, values, cut_month, *, future_blank=True, reason=None) -> list[Cell]:
    cells = []
    for m in months:
        v = values.get(m)
        if v is not None:
            cells.append(Cell(month=m, value=v, status="actual"))
        elif future_blank and m > cut_month:
            cells.append(Cell(month=m, value=None, status="future", reason=FUTURE))
        else:
            cells.append(Cell(month=m, value=None, status="unavailable", reason=reason))
    return cells


def _sum(values) -> Decimal | None:
    real = [v for v in values.values() if v is not None]
    return sum(real) if real else None


@router.get("/managers/{manager}/detail", response_model=ManagerDetail,
            tags=["managers"])
def manager_detail(manager: str, financial_year: int = Query(2026),
                   user=Depends(current_user)):
    """Everything about one account manager for one financial year."""
    who = fetch_one("""SELECT canonical_manager, status, include_in_rankings
                       FROM reporting_manager WHERE canonical_manager = %(m)s""",
                    {"m": manager})
    if who is None:
        raise HTTPException(404, f"unknown manager '{manager}'")

    cut_month = fetch_one("""SELECT date_trunc('month', cut_off_date)::date AS m
                             FROM reporting_settings WHERE id = 1""")["m"]
    months = _fy_months(financial_year)
    params = {"m": manager, "fy": financial_year, "py": financial_year - 1}

    # --- transaction grid ----------------------------------------------------
    grid: dict[str, dict] = {label: {} for label in ROW_ORDER}
    for r in fetch_all("""
            SELECT business_classification, period_month, SUM(actual_income) AS amount
            FROM v_sales_reported
            WHERE canonical_manager = %(m)s AND financial_year = %(fy)s
            GROUP BY 1, 2""", params):
        grid.setdefault(r["business_classification"], {})[r["period_month"]] = r["amount"]

    actual = {r["period_month"]: r["net_actual_income"] for r in fetch_all("""
        SELECT period_month, net_actual_income FROM v_actual_month
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s""", params)}
    positive = {r["period_month"]: r["positive_actual_income"] for r in fetch_all("""
        SELECT period_month, positive_actual_income FROM v_actual_month
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s""", params)}
    returns = {r["period_month"]: r["absolute_return_income"] for r in fetch_all("""
        SELECT period_month, absolute_return_income FROM v_actual_month
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s""", params)}
    renewal_count = {r["period_month"]: Decimal(r["n"]) for r in fetch_all("""
        SELECT period_month, count(*) AS n FROM v_sales_reported
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s
          AND category IN ('RWL','TRW')
        GROUP BY 1""", params)}

    original = {r["forecast_month"]: r["original_forecast"] for r in fetch_all("""
        SELECT forecast_month, SUM(original_forecast) AS original_forecast
        FROM v_forecast_position_month
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s
        GROUP BY 1""", params)}
    latest = {r["forecast_month"]: r["latest_forecast"] for r in fetch_all("""
        SELECT forecast_month, SUM(latest_forecast) AS latest_forecast
        FROM v_forecast_position_month
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s
        GROUP BY 1""", params)}
    budget = {r["forecast_month"]: r["total_budget"] for r in fetch_all("""
        SELECT forecast_month, total_budget FROM v_monthly_budget
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s""", params)}
    nb_target = {r["forecast_month"]: r["new_business_growth_target"] for r in fetch_all("""
        SELECT forecast_month, new_business_growth_target FROM v_monthly_budget
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s""", params)}

    # The growth percentage actually in force, and where in the hierarchy it
    # came from. Held per quarter, shown against every month in that quarter.
    growth_rows = fetch_all("""
        SELECT financial_quarter, growth_pct, dollar_override, growth_basis
        FROM v_budget_quarter
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s""", params)
    growth_by_quarter = {r["financial_quarter"]: r for r in growth_rows}
    growth_pct_by_month = {}
    for m in months:
        g = growth_by_quarter.get(australian_quarter(m))
        if g and g["growth_pct"] is not None and g["dollar_override"] is None:
            growth_pct_by_month[m] = g["growth_pct"]
    active_basis = next((r["growth_basis"] for r in growth_rows), None)
    active_growth = next((r["growth_pct"] for r in growth_rows
                          if r["growth_pct"] is not None), None)
    # Per-quarter view of the rate in force, so the control can show what it is
    # about to change rather than only the headline.
    quarter_growth = [{"financial_quarter": r["financial_quarter"],
                       "growth_pct": r["growth_pct"],
                       "growth_basis": r["growth_basis"],
                       "dollar_override": r["dollar_override"]}
                      for r in sorted(growth_rows,
                                      key=lambda x: x["financial_quarter"])]

    prior = {}
    for r in fetch_all("""
            SELECT period_month, net_actual_income FROM v_actual_month
            WHERE canonical_manager = %(m)s AND financial_year = %(py)s""", params):
        shifted = dt.date(r["period_month"].year + 1, r["period_month"].month, 1)
        prior[shifted] = r["net_actual_income"]

    # --- rows ----------------------------------------------------------------
    rows: list[GridRow] = []
    for label in ROW_ORDER:
        values = grid.get(label, {})
        if not values and label not in ("Renewal", "Transfer Renewal", "New Business"):
            continue  # keep the grid readable: drop categories with no activity
        rows.append(GridRow(label=label, kind="transaction",
                            cells=_cells(months, values, cut_month),
                            total=_sum(values)))

    rows.append(GridRow(label="Positive Actual Income", kind="derived",
                        cells=_cells(months, positive, cut_month),
                        total=_sum(positive),
                        hint="Sum of positive transactions before returns."))
    # Shown signed. It is money going back out, so it should read as a loss
    # rather than as another positive figure sitting beside income.
    signed_returns = {m: -v for m, v in returns.items() if v is not None}
    rows.append(GridRow(label="Return Income", kind="derived",
                        cells=_cells(months, signed_returns, cut_month),
                        total=_sum(signed_returns),
                        hint="Money returned: lapses, cancellations, negative "
                             "endorsements and corrections. Shown as a negative "
                             "because it reduces Net Actual Income."))
    rows.append(GridRow(label="Net Actual Income", kind="total",
                        cells=_cells(months, actual, cut_month), total=_sum(actual),
                        hint="Positive income less return income. This is the primary "
                             "actual performance measure."))

    rows.append(GridRow(label="Renewal Forecast", kind="forecast",
                        cells=_cells(months, original, cut_month, future_blank=False,
                                     reason=NO_BASELINE),
                        total=_sum(original),
                        hint="Frozen at baseline. Never changed by a later upload."))
    # Variance and achievement only where both sides exist for a completed month.
    variance, achievement = {}, {}
    for m in months:
        a, o = actual.get(m), original.get(m)
        if m <= cut_month and a is not None and o is not None:
            variance[m] = a - o
            if o != 0:
                achievement[m] = a / o
    rows.append(GridRow(label="Variance to Renewal Forecast", kind="derived",
                        cells=_cells(months, variance, cut_month), total=_sum(variance)))
    rows.append(GridRow(label="Forecast Achievement", kind="derived",
                        value_kind="percent",
                        cells=_cells(months, achievement, cut_month), total=None,
                        hint="Net Actual Income divided by Renewal Forecast."))

    rows.append(GridRow(
        label="Growth % applied", kind="budget", value_kind="percent",
        cells=_cells(months, growth_pct_by_month, cut_month, future_blank=False,
                     reason="A dollar override is in force for this quarter, so no "
                            "percentage applies."),
        total=None,
        hint=("The new business growth percentage set for this manager. Budget = "
              "Renewal Forecast + (Renewal Forecast x this percentage). Change it "
              "in the panel above." )))

    rows.append(GridRow(label="New Business Growth Target", kind="budget",
                        cells=_cells(months, nb_target, cut_month, future_blank=False,
                                     reason=NO_BUDGET),
                        total=_sum(nb_target),
                        hint="Allocated across the quarter by each month's share of the "
                             "Original Renewal Forecast, not in equal thirds."))
    rows.append(GridRow(label="Total Budget", kind="budget",
                        cells=_cells(months, budget, cut_month, future_blank=False,
                                     reason=NO_BUDGET),
                        total=_sum(budget),
                        hint="Original Renewal Forecast plus the growth target. Does not "
                             "move when the Latest Forecast moves."))

    budget_var, budget_ach = {}, {}
    for m in months:
        a, b = actual.get(m), budget.get(m)
        if m <= cut_month and a is not None and b is not None:
            budget_var[m] = a - b
            if b != 0:
                budget_ach[m] = a / b
    rows.append(GridRow(label="Budget Achievement", kind="derived",
                        value_kind="percent",
                        cells=_cells(months, budget_ach, cut_month), total=None))

    # Made or missed, stated plainly. 1 = YES, 0 = NO; the interface colours it.
    achieved = {m: (Decimal(1) if budget_ach[m] >= 1 else Decimal(0))
                for m in budget_ach}
    rows.append(GridRow(
        label="Budget Achieved?", kind="total", value_kind="verdict",
        cells=_cells(months, achieved, cut_month), total=None,
        hint="YES where Net Actual Income reached the Total Budget for that month."))

    # Measured against Total Budget, which is the renewal forecast grown by the
    # manager's own growth percentage. Set the growth to 10% and this row answers
    # "did the income clear forecast plus 10%, and by how much".
    over_under = {m: budget_ach[m] - 1 for m in budget_ach}
    rows.append(GridRow(
        label="% Above / (Below) Target", kind="total", value_kind="percent",
        cells=_cells(months, over_under, cut_month), total=None,
        hint="Actual income against Total Budget, which is the Renewal Forecast "
             "grown by this manager's Growth % applied. Positive means the growth "
             "target was cleared."))
    rows.append(GridRow(
        label="$ Above / (Below) Target", kind="total", value_kind="money",
        cells=_cells(months, budget_var, cut_month), total=_sum(budget_var),
        hint="The same comparison in dollars: actual income less Total Budget."))

    bonus = {r["period_month"]: r["indicative_bonus"] for r in fetch_all("""
        SELECT period_month, indicative_bonus FROM v_bonus_month
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s
          AND indicative_bonus IS NOT NULL""", params)}
    rows.append(GridRow(
        label="Bonus (indicative)", kind="total", value_kind="money",
        cells=_cells(months, bonus, cut_month), total=_sum(bonus),
        hint="Indicative only. The bonus is a quarterly entitlement, so these "
             "monthly figures do not sum to what actually pays: a quarter can be "
             "missed overall while individual months within it ran ahead. The "
             "Bonus Tracker holds the quarterly figure."))

    rows.append(GridRow(label="Prior Year Actual (same month)", kind="prior",
                        cells=_cells(months, prior, cut_month, future_blank=False,
                                     reason="No prior-year actual for this month."),
                        total=_sum(prior)))
    py_change = {m: actual[m] - prior[m] for m in months
                 if actual.get(m) is not None and prior.get(m) is not None}
    rows.append(GridRow(label="Increase / (Decrease) on Prior Year", kind="derived",
                        cells=_cells(months, py_change, cut_month), total=_sum(py_change)))

    rows.append(GridRow(label="Renewal Transactions (count)", kind="derived",
                        value_kind="count",
                        cells=_cells(months, renewal_count, cut_month),
                        total=_sum(renewal_count),
                        hint="Renewal and transfer-renewal transaction lines, not unique "
                             "policies. A policy can generate several lines."))

    # --- quarter roll-up -----------------------------------------------------
    quarters = []
    for q in (1, 2, 3, 4):
        q_months = months[(q - 1) * 3:q * 3]
        q_actual = {m: actual[m] for m in q_months if actual.get(m) is not None}
        q_budget = {m: budget[m] for m in q_months if budget.get(m) is not None}
        q_orig = {m: original[m] for m in q_months if original.get(m) is not None}
        a, b, o = _sum(q_actual), _sum(q_budget), _sum(q_orig)
        started = any(m <= cut_month for m in q_months)
        quarters.append({
            "quarter": q,
            "months": q_months,
            "started": started,
            "net_actual_income": a,
            "original_forecast": o,
            "total_budget": b,
            "variance": (a - b) if (started and a is not None and b is not None) else None,
            "achievement": (a / b) if (started and a is not None and b) else None,
            "achieved": (bool(a >= b) if (started and a is not None and b is not None)
                         else None),
            "over_under_pct": ((a / b) - 1 if (started and a is not None and b)
                               else None),
        })

    # --- headline figures ----------------------------------------------------
    ytd_months = [m for m in months if m <= cut_month]
    ytd_actual = _sum({m: actual[m] for m in ytd_months if actual.get(m) is not None})
    ytd_budget = _sum({m: budget[m] for m in ytd_months if budget.get(m) is not None})
    py_total = fetch_one("""SELECT SUM(net_actual_income) AS total FROM v_actual_month
                            WHERE canonical_manager = %(m)s
                              AND financial_year = %(py)s""", params)["total"]
    outlook = fetch_one("""SELECT SUM(latest_outlook) AS outlook,
                                  SUM(remaining_budget_gap) AS gap
                           FROM v_outlook_quarter
                           WHERE canonical_manager = %(m)s
                             AND financial_year = %(fy)s""", params)

    full_budget = _sum(budget)
    full_original = _sum(original)

    notes = []
    if financial_year == 2026:
        notes.append("July 2026 uses supplied per-manager forecast figures at "
                     "manager-month level. Policy-level renewal detail begins "
                     "August 2026.")
    if not who["include_in_rankings"]:
        notes.append(f"{manager} is excluded from rankings by default. Actual income "
                     "still counts towards business totals.")

    return ManagerDetail(
        canonical_manager=who["canonical_manager"], status=who["status"],
        include_in_rankings=who["include_in_rankings"],
        financial_year=financial_year,
        financial_year_label=f"FY{financial_year}-{str(financial_year + 1)[2:]}",
        months=months,
        month_status=["completed" if m <= cut_month else "future" for m in months],
        cut_off_month=cut_month,
        prior_year_actual=Money.of(py_total, "No prior-year actuals for this manager."),
        ytd_actual=Money.of(ytd_actual, FUTURE),
        ytd_budget=Money.of(ytd_budget, NO_BUDGET),
        ytd_achievement=Ratio.of(
            (ytd_actual / ytd_budget) if (ytd_actual is not None and ytd_budget) else None,
            NO_BUDGET),
        full_year_budget=Money.of(full_budget, NO_BUDGET),
        full_year_original_forecast=Money.of(full_original, NO_BASELINE),
        full_year_latest_forecast=Money.of(_sum(latest), NO_BASELINE),
        latest_outlook=Money.of(outlook["outlook"] if outlook else None),
        remaining_budget_gap=Money.of(outlook["gap"] if outlook else None, NO_BUDGET),
        forecast_achievement=Ratio.of(
            (ytd_actual / _sum({m: original[m] for m in ytd_months
                                if original.get(m) is not None}))
            if ytd_actual is not None
            and _sum({m: original[m] for m in ytd_months if original.get(m) is not None})
            else None, NO_BASELINE),
        active_growth_pct=Ratio.of(
            active_growth,
            "A direct dollar override is in force, so no percentage applies."),
        active_growth_basis=active_basis,
        quarter_growth=quarter_growth,
        rows=rows, quarters=quarters, meta=meta(financial_year, notes))
