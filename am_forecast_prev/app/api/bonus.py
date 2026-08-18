"""Bonus.

    Budget Target      = Expected Income x (1 + Growth %)
    Base Bonus         = (Budget Target - Expected Income) / divisor
    Above-Target Bonus = (Actual Income - Budget Target) x rate
    Total              = 0 below target, otherwise Base + Above-Target

The arithmetic lives in `v_bonus_quarter` and `v_bonus_month`, not here. This
module selects, aggregates and labels.

Two things it is careful about.

**Earned is not projected.** A quarter still running has no settled bonus, so the
earned figure is what would pay if the quarter closed today — usually zero
part-way through, which is the truth. The projection is reported separately and
always labelled.

**Monthly bonus is indicative.** The entitlement is quarterly. Monthly figures do
not sum to the quarterly bonus, because a quarter can be missed overall while
individual months within it ran ahead. Both are shown; only the quarterly one
pays.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .core import (
    DSN, GST_NOTE, current_user, fetch_all, fetch_one, meta, require_admin,
)

router = APIRouter()

QUARTER_MONTHS = {1: "Jul-Sep", 2: "Oct-Dec", 3: "Jan-Mar", 4: "Apr-Jun"}


def _dec(v) -> Decimal:
    return Decimal(str(v or 0))


def _scheme() -> dict:
    r = fetch_one("""SELECT bonus_base_divisor, bonus_above_target_rate
                     FROM reporting_settings WHERE id = 1""")
    # Decimal keeps its scale through :g, so 3 prints as "3.00" and 0.2 as
    # "20.0000%". Cast for display only; the stored values stay exact.
    divisor = float(r["bonus_base_divisor"])
    rate_pct = float(r["bonus_above_target_rate"]) * 100
    return {
        "base_divisor": r["bonus_base_divisor"],
        "above_target_rate": r["bonus_above_target_rate"],
        "description": (
            f"Bonus applies only once the quarter's Budget Target is reached. "
            f"Base bonus is the monetary growth target divided by {divisor:g}. "
            f"Income above the target earns a further {rate_pct:g}%."),
        "formula": [
            "Budget Target = Expected Income x (1 + Growth %)",
            f"Base Bonus = (Budget Target - Expected Income) / {divisor:g}",
            f"Above-Target Bonus = (Actual Income - Budget Target) x {rate_pct:g}%",
            "Total = 0 below target, otherwise Base + Above-Target",
        ],
    }


class BonusQuarter(BaseModel):
    canonical_manager: str
    financial_year: int
    financial_quarter: int
    quarter_label: str
    months_in_quarter: int
    months_elapsed: int
    quarter_started: bool
    quarter_complete: bool

    expected_income: Decimal | None = None
    growth_pct: Decimal | None = None
    growth_target_amount: Decimal | None = None
    budget_target: Decimal | None = None
    actual_income: Decimal | None = None
    above_below_target: Decimal | None = None
    target_achievement: Decimal | None = None
    target_reached: bool | None = None
    income_still_required: Decimal | None = None

    bonus_at_target: Decimal | None = None
    base_bonus: Decimal | None = None
    above_target_bonus: Decimal | None = None
    total_bonus: Decimal | None = None
    projected_income: Decimal | None = None
    projected_bonus: Decimal | None = None
    status: str = "not started"


def _status(r: dict) -> str:
    if not r["quarter_started"]:
        return "not started"
    if r["quarter_complete"]:
        return "earned" if r["target_reached"] else "missed"
    return "on track" if (r["projected_bonus"] or 0) > 0 else "behind"


def _quarter_rows(financial_year: int, manager: str | None = None) -> list[dict]:
    params: dict = {"fy": financial_year}
    clause = ""
    if manager:
        clause = " AND b.canonical_manager = %(m)s"
        params["m"] = manager
    return fetch_all(f"""
        SELECT b.*, m.include_in_rankings, m.status AS manager_status
        FROM v_bonus_quarter b
        JOIN reporting_manager m ON m.canonical_manager = b.canonical_manager
        WHERE b.financial_year = %(fy)s{clause}
        ORDER BY b.canonical_manager, b.financial_quarter""", params)


@router.get("/bonus", tags=["bonus"])
def bonus(financial_year: int = Query(2026), manager: str | None = None,
          include_non_ranked: bool = Query(False), user=Depends(current_user)):
    """Quarterly bonus for every manager, with year-to-date totals."""
    rows = [r for r in _quarter_rows(financial_year, manager)
            if include_non_ranked or r["include_in_rankings"]]

    quarters = [BonusQuarter(
        **{k: r[k] for k in BonusQuarter.model_fields if k in r
           and k not in ("quarter_label", "status")},
        quarter_label=f"Q{r['financial_quarter']} {QUARTER_MONTHS[r['financial_quarter']]}",
        status=_status(r)).model_dump() for r in rows]

    by_manager: dict[str, dict] = {}
    for r in rows:
        acc = by_manager.setdefault(r["canonical_manager"], {
            "canonical_manager": r["canonical_manager"],
            "include_in_rankings": r["include_in_rankings"],
            "manager_status": r["manager_status"],
            "expected_income": Decimal(0), "budget_target": Decimal(0),
            "actual_income": Decimal(0), "bonus_at_target": Decimal(0),
            "earned_bonus": Decimal(0), "projected_bonus": Decimal(0),
            "quarters_earned": 0, "quarters_missed": 0, "quarters_open": 0,
            "quarters_started": 0, "quarters_total": 0,
            # Year to date counts only quarters that have begun, so an untouched
            # Q4 does not drag a manager's position down.
            "ytd_expected": Decimal(0), "ytd_budget_target": Decimal(0),
            "ytd_actual": Decimal(0),
        })
        acc["expected_income"] += _dec(r["expected_income"])
        acc["budget_target"] += _dec(r["budget_target"])
        acc["actual_income"] += _dec(r["actual_income"])
        acc["bonus_at_target"] += _dec(r["bonus_at_target"])
        acc["earned_bonus"] += _dec(r["total_bonus"])
        # The projection covers started quarters only. A quarter that has not
        # begun cannot be projected from anything, so it contributes nothing
        # rather than an assumed result.
        acc["projected_bonus"] += _dec(r["projected_bonus"] if r["projected_bonus"]
                                       is not None else r["total_bonus"])
        acc["quarters_total"] += 1
        if r["quarter_started"]:
            acc["quarters_started"] += 1
            acc["ytd_expected"] += _dec(r["expected_income"])
            acc["ytd_budget_target"] += _dec(r["budget_target"])
            acc["ytd_actual"] += _dec(r["actual_income"])
            if r["quarter_complete"]:
                if r["target_reached"]:
                    acc["quarters_earned"] += 1
                else:
                    acc["quarters_missed"] += 1
            else:
                acc["quarters_open"] += 1

    # Full-year outlook: the projection for quarters under way, plus the base
    # bonus for quarters not yet begun. Stated as an assumption rather than
    # presented as a forecast, because the later quarters assume target is met.
    for m in by_manager.values():
        not_started = m["quarters_total"] - m["quarters_started"]
        m["quarters_not_started"] = not_started
        m["full_year_outlook"] = m["projected_bonus"] + (
            m["bonus_at_target"] * Decimal(not_started) / Decimal(m["quarters_total"])
            if m["quarters_total"] else Decimal(0))

    managers = sorted(by_manager.values(),
                      key=lambda m: -(m["earned_bonus"] + m["projected_bonus"]))

    totals = {
        "expected_income": sum((m["expected_income"] for m in managers), Decimal(0)),
        "budget_target": sum((m["budget_target"] for m in managers), Decimal(0)),
        "actual_income": sum((m["actual_income"] for m in managers), Decimal(0)),
        "bonus_at_target": sum((m["bonus_at_target"] for m in managers), Decimal(0)),
        "earned_bonus": sum((m["earned_bonus"] for m in managers), Decimal(0)),
        "projected_bonus": sum((m["projected_bonus"] for m in managers), Decimal(0)),
        "full_year_outlook": sum((m["full_year_outlook"] for m in managers), Decimal(0)),
        "managers": len(managers),
    }

    started_quarters = sorted({q["financial_quarter"] for q in quarters
                               if q["quarter_started"]})
    started_label = (", ".join(f"Q{q}" for q in started_quarters)
                     if started_quarters else "none yet")
    scope = {
        "earned_bonus": "Payable on the figures to date. A quarter still open "
                        "normally shows nil, because the target is judged over the "
                        "whole quarter.",
        "projected_bonus": (
            f"Covers only the quarters under way ({started_label}), projected at "
            "the pace of the months completed. Quarters that have not begun "
            "contribute nothing. Not money earned."),
        "bonus_at_target": "All four quarters, base bonus only, assuming each target "
                           "is met exactly and nothing is earned above it.",
        "full_year_outlook": "Projection for quarters under way, plus base bonus for "
                             "quarters not yet begun. Assumes later targets are met.",
    }

    return {
        "financial_year": financial_year,
        "financial_year_label": f"FY{financial_year}-{str(financial_year + 1)[2:]}",
        "scheme": _scheme(),
        "quarters": quarters,
        "managers": managers,
        "totals": totals,
        "column_scope": scope,
        "meta": meta(financial_year, notes=[
            "Bonus is a quarterly entitlement. A quarter still running shows the "
            "bonus that would pay if it closed today, which is usually nil "
            "part-way through, alongside a separate projection at the current "
            "pace.",
            "Projections are not money earned. They assume the pace of the months "
            "completed continues for the rest of the quarter.",
            "The three bonus columns cover different periods and are not "
            "comparable with each other: Earned and Projected cover quarters that "
            "have started, while At target covers the whole year.",
        ]),
        "gst_note": GST_NOTE,
    }


@router.get("/bonus/{manager}", tags=["bonus"])
def bonus_for_manager(manager: str, financial_year: int = Query(2026),
                      user=Depends(current_user)):
    """One manager's bonus position, quarter by quarter and month by month."""
    who = fetch_one("""SELECT canonical_manager FROM reporting_manager
                       WHERE canonical_manager = %(m)s""", {"m": manager})
    if who is None:
        raise HTTPException(404, f"unknown manager '{manager}'")

    rows = _quarter_rows(financial_year, manager)
    quarters = [BonusQuarter(
        **{k: r[k] for k in BonusQuarter.model_fields if k in r
           and k not in ("quarter_label", "status")},
        quarter_label=f"Q{r['financial_quarter']} {QUARTER_MONTHS[r['financial_quarter']]}",
        status=_status(r)).model_dump() for r in rows]

    months = fetch_all("""
        SELECT period_month, financial_quarter, month_started, expected_income,
               budget_target, growth_target_amount, actual_income, target_reached,
               indicative_bonus
        FROM v_bonus_month
        WHERE canonical_manager = %(m)s AND financial_year = %(fy)s
        ORDER BY period_month""", {"m": manager, "fy": financial_year})

    earned = sum((_dec(q["total_bonus"]) for q in quarters), Decimal(0))
    at_target = sum((_dec(q["bonus_at_target"]) for q in quarters), Decimal(0))
    projected = sum((_dec(q["projected_bonus"] if q["projected_bonus"] is not None
                          else q["total_bonus"]) for q in quarters), Decimal(0))

    return {
        "canonical_manager": manager,
        "financial_year": financial_year,
        "financial_year_label": f"FY{financial_year}-{str(financial_year + 1)[2:]}",
        "scheme": _scheme(),
        "quarters": quarters,
        "months": months,
        "totals": {"earned_bonus": earned, "bonus_at_target": at_target,
                   "projected_bonus": projected,
                   "quarters_earned": sum(1 for q in quarters if q["status"] == "earned"),
                   "quarters_missed": sum(1 for q in quarters if q["status"] == "missed")},
        "meta": meta(financial_year, notes=[
            "Monthly bonus is indicative only. The entitlement is quarterly, and "
            "monthly figures do not sum to it: a quarter can be missed overall "
            "while individual months within it ran ahead.",
        ]),
        "gst_note": GST_NOTE,
    }


class SchemeBody(BaseModel):
    base_divisor: Decimal = Field(gt=0)
    above_target_rate: Decimal = Field(ge=0, le=1)
    reason: str = Field(min_length=3)


@router.post("/bonus/settings", tags=["bonus"])
def set_scheme(body: SchemeBody, user=Depends(require_admin)):
    """Change the bonus scheme.

    Held as settings rather than constants so a change of scheme is an
    administrator's decision, recorded with a reason, rather than a code change.
    """
    import json

    import psycopg2
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT bonus_base_divisor, bonus_above_target_rate
                           FROM reporting_settings WHERE id = 1""")
            before = cur.fetchone()
            cur.execute("""UPDATE reporting_settings
                           SET bonus_base_divisor = %s, bonus_above_target_rate = %s
                           WHERE id = 1""",
                        (body.base_divisor, body.above_target_rate))
            cur.execute("""
                INSERT INTO budget_audit (action, scope_description, before_value,
                                          after_value, reason, performed_by)
                VALUES ('set_bonus_scheme', 'Bonus scheme', %s::jsonb, %s::jsonb, %s, %s)""",
                        (json.dumps({"base_divisor": str(before[0]),
                                     "above_target_rate": str(before[1])}),
                         json.dumps({"base_divisor": str(body.base_divisor),
                                     "above_target_rate": str(body.above_target_rate)}),
                         body.reason, user.username))
    return {"before": {"base_divisor": before[0], "above_target_rate": before[1]},
            "after": {"base_divisor": body.base_divisor,
                      "above_target_rate": body.above_target_rate},
            "note": "Applies to every quarter immediately, including closed ones."}
