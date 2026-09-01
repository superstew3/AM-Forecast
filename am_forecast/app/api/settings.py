"""Settings and reference maintenance.

Two problems this solves, both about the system still working in five years.

**Nothing derives the financial year from a hardcoded constant.** Every year
list, label and default comes from the data. Without this the interface would
have needed editing each July, which is exactly the kind of change that does not
get made and quietly produces a screen showing last year.

**The mappings that accumulate can be maintained without a developer.** New
policy classes, renamed managers and new exclusion rules arrive with every
insurer export. Each has an endpoint here, gated to administrators and audited.
"""
from __future__ import annotations

import datetime as dt
import json
import re

import psycopg2

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .core import (
    DSN, GST_NOTE, current_user, fetch_all, fetch_one, meta, require_admin,
)

router = APIRouter()


def _fy_label(fy: int) -> str:
    return f"FY{fy}-{str(fy + 1)[2:]}"


QUARTERS = [
    {"quarter": 1, "label": "Q1", "months": "Jul-Sep"},
    {"quarter": 2, "label": "Q2", "months": "Oct-Dec"},
    {"quarter": 3, "label": "Q3", "months": "Jan-Mar"},
    {"quarter": 4, "label": "Q4", "months": "Apr-Jun"},
]


@router.get("/periods", tags=["system"])
def periods(user=Depends(current_user)):
    """Financial years present in the data, and which one is current.

    The interface builds every year selector from this. Nothing is hardcoded, so
    the app rolls into a new financial year on its own.
    """
    # The year selector on every page is built from this. Deriving it from the
    # stored cut-off meant the app would have kept offering last financial year
    # as "current" until somebody advanced a setting -- and the cut-off has
    # decided nothing since migration 0020.
    cut = fetch_one("""SELECT cut_off_date,
                              au_financial_year(reporting_current_month()) AS current_fy,
                              au_quarter(reporting_current_month()) AS current_quarter,
                              reporting_current_month() AS cut_month
                       FROM reporting_settings WHERE id = 1""")

    years = fetch_all("""
        SELECT fy AS financial_year,
               bool_or(has_actuals) AS has_actuals,
               bool_or(has_forecast) AS has_forecast,
               MIN(first_month) AS first_month,
               MAX(last_month) AS last_month,
               SUM(month_count) AS months_present
        FROM (
            SELECT financial_year AS fy, true AS has_actuals, false AS has_forecast,
                   MIN(period_month) AS first_month, MAX(period_month) AS last_month,
                   COUNT(DISTINCT period_month) AS month_count
            FROM v_actual_month GROUP BY financial_year
            UNION ALL
            SELECT financial_year, false, true,
                   MIN(forecast_month), MAX(forecast_month),
                   COUNT(DISTINCT forecast_month)
            FROM v_original_forecast_month GROUP BY financial_year
        ) x
        GROUP BY fy ORDER BY fy DESC""")

    coverage = {(r["financial_year"], r["data_domain"]): r for r in fetch_all(
        """SELECT financial_year, data_domain, coverage_status, months_present, label
           FROM period_coverage""")}

    out = []
    for y in years:
        fy = y["financial_year"]
        actuals = coverage.get((fy, "actuals"), {})
        out.append({
            "financial_year": fy,
            "label": _fy_label(fy),
            "has_actuals": y["has_actuals"],
            "has_forecast": y["has_forecast"],
            "is_current": fy == cut["current_fy"],
            "coverage_status": actuals.get("coverage_status"),
            "coverage_note": actuals.get("label"),
            "first_month": y["first_month"],
            "last_month": y["last_month"],
        })

    return {
        "current_financial_year": cut["current_fy"],
        "current_financial_year_label": _fy_label(cut["current_fy"]),
        "current_quarter": cut["current_quarter"],
        "cut_off_date": cut["cut_off_date"],
        "cut_off_month": cut["cut_month"],
        "financial_years": out,
        "quarters": QUARTERS,
        "gst_note": GST_NOTE,
    }


class CutOffBody(BaseModel):
    cut_off_date: dt.date
    reason: str = Field(min_length=3)


@router.post("/settings/cut-off", tags=["system"])
def set_cut_off(body: CutOffBody, user=Depends(require_admin)):
    """Move the Reporting Cut-Off Date.

    This is the line between completed and future periods, so it governs what
    counts as an actual, what is still Pending, and which months are measured.
    Moving it backwards past data that already exists is refused: a month with
    transactions in it is complete, and pretending otherwise would hide income.
    """
    latest = fetch_one("""SELECT MAX(transaction_date)::date AS d
                          FROM sales_transaction WHERE NOT is_excluded""")["d"]
    if latest and body.cut_off_date < latest:
        raise HTTPException(
            409,
            f"Cut-off {body.cut_off_date} precedes the latest transaction {latest}. "
            "Those months are complete; moving the cut-off behind them would hide "
            "actual income. Choose a date on or after the last transaction.")

    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cut_off_date FROM reporting_settings WHERE id = 1")
            previous = cur.fetchone()[0]
            cur.execute("""UPDATE reporting_settings
                           SET cut_off_date = %s, cut_off_set_by = %s,
                               cut_off_set_at = now() WHERE id = 1""",
                        (body.cut_off_date, user.username))
            cur.execute("""
                INSERT INTO budget_audit (action, scope_description, before_value,
                                          after_value, reason, performed_by)
                VALUES ('set_cut_off_date', 'Reporting Cut-Off Date',
                        %s::jsonb, %s::jsonb, %s, %s)""",
                        (json.dumps({"cut_off_date": str(previous)}),
                         json.dumps({"cut_off_date": str(body.cut_off_date)}),
                         body.reason, user.username))
    return {"previous": previous, "cut_off_date": body.cut_off_date,
            "set_by": user.username,
            "note": "Achievement, Pending status and the completed/future split all "
                    "move with this date."}


# --- reference maintenance ----------------------------------------------------

@router.get("/reference/mappings", tags=["reference"])
def mappings(user=Depends(current_user)):
    """Everything the calculations depend on, in one place."""
    return {
        "manager_aliases": fetch_all("""
            SELECT a.id, a.source_manager, a.canonical_manager, a.active, a.note,
                   m.status, m.include_in_rankings
            FROM manager_alias a
            JOIN reporting_manager m ON m.canonical_manager = a.canonical_manager
            ORDER BY a.canonical_manager, a.source_manager"""),
        "unmapped_managers": fetch_all("""
            SELECT DISTINCT source_manager, count(*) AS transactions
            FROM v_sales_reported WHERE canonical_manager IS NULL
            GROUP BY 1 ORDER BY 2 DESC"""),
        "class_equivalence": fetch_all("""
            SELECT id, source_type, source_value, canonical_class, note
            FROM class_equivalence ORDER BY canonical_class, source_type"""),
        "unmapped_classes": fetch_all("""
            SELECT 'renewals' AS source_type, upper(trim(class_abbrev)) AS source_value,
                   count(*) AS records
            FROM forecast_policy p
            WHERE NOT p.is_excluded AND NOT EXISTS (
                SELECT 1 FROM class_equivalence e WHERE e.source_type='renewals'
                  AND e.source_value = upper(trim(p.class_abbrev)))
            GROUP BY 1, 2
            UNION ALL
            SELECT 'sales', upper(trim(t.policy_class)), count(*)
            FROM sales_transaction t
            WHERE NOT t.is_excluded AND t.policy_class IS NOT NULL
              AND t.category IN ('RWL','TRW')
              AND NOT EXISTS (
                SELECT 1 FROM class_equivalence e WHERE e.source_type='sales'
                  AND e.source_value = upper(trim(t.policy_class)))
            GROUP BY 1, 2
            ORDER BY records DESC"""),
        "category_map": fetch_all("""
            SELECT category, business_classification, description, active
            FROM category_map ORDER BY category"""),
        "exclusion_rules": fetch_all("""
            SELECT id, rule_group, rule_name, source_type, target_field, match_type,
                   match_value, active, note
            FROM exclusion_rule ORDER BY source_type, target_field, match_value"""),
        "canonical_classes": fetch_all("""
            SELECT DISTINCT canonical_class FROM class_equivalence
            ORDER BY canonical_class"""),
        "meta": meta(),
    }


def _normalise(value: str) -> str:
    s = re.sub(r"[^A-Z0-9 ]", " ", (value or "").upper())
    return re.sub(r"\s+", " ", s).strip()


class ClassEquivalenceBody(BaseModel):
    source_type: str = Field(pattern="^(sales|renewals)$")
    source_value: str = Field(min_length=1)
    canonical_class: str = Field(min_length=1)
    note: str | None = None


@router.post("/reference/class-equivalence", tags=["reference"])
def add_class_equivalence(body: ClassEquivalenceBody, user=Depends(require_admin)):
    """Map a policy class so it can reach the top matching tier.

    The two sources use different class vocabularies, and new values arrive with
    every insurer export. Mapping them is a five-minute administrator task, not a
    code change.
    """
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO class_equivalence
                    (source_type, source_value, canonical_class, note, updated_by)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (source_type, source_value) DO UPDATE SET
                    canonical_class = EXCLUDED.canonical_class,
                    note = EXCLUDED.note, updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                RETURNING id""",
                        (body.source_type, body.source_value.strip().upper(),
                         body.canonical_class.strip().upper(), body.note, user.username))
            new_id = cur.fetchone()[0]
    return {"id": new_id, "source_value": body.source_value.strip().upper(),
            "canonical_class": body.canonical_class.strip().upper(),
            "note": "Re-run matching to apply this to existing records."}


class AliasBody(BaseModel):
    source_manager: str = Field(min_length=1)
    canonical_manager: str = Field(min_length=1)
    note: str | None = None


@router.post("/reference/manager-alias", tags=["reference"])
def add_manager_alias(body: AliasBody, user=Depends(require_admin)):
    """Point a source manager name at a canonical reporting manager.

    Applied by join at read time, so adding an alias retrospectively corrects
    actuals, forecasts and budgets together rather than only new records.
    """
    exists = fetch_one("""SELECT 1 AS x FROM reporting_manager
                          WHERE canonical_manager = %(m)s""",
                       {"m": body.canonical_manager})
    if not exists:
        raise HTTPException(
            400,
            f"'{body.canonical_manager}' is not a reporting manager. Create the "
            "reporting manager first, or map to an existing one.")
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO manager_alias
                    (source_manager, source_manager_norm, canonical_manager,
                     active, note, updated_by)
                VALUES (%s, %s, %s, true, %s, %s)
                ON CONFLICT (source_manager) DO UPDATE SET
                    canonical_manager = EXCLUDED.canonical_manager,
                    note = EXCLUDED.note, updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                RETURNING id""",
                        (body.source_manager.strip(), _normalise(body.source_manager),
                         body.canonical_manager, body.note, user.username))
            new_id = cur.fetchone()[0]
    return {"id": new_id, "source_manager": body.source_manager.strip(),
            "canonical_manager": body.canonical_manager,
            "note": "Applied immediately to every period, past and future."}


class ManagerFlagsBody(BaseModel):
    canonical_manager: str
    include_in_rankings: bool | None = None
    include_in_business_totals: bool | None = None
    status: str | None = Field(default=None,
                               pattern="^(active|legacy_unmapped|inactive)$")
    note: str | None = None


@router.post("/reference/manager-flags", tags=["reference"])
def set_manager_flags(body: ManagerFlagsBody, user=Depends(require_admin)):
    """Change whether a manager appears in rankings or business totals.

    Deliberately two separate flags. Anastasia K counts towards business totals
    but not rankings, because those answer different questions.
    """
    sets, params = [], {"m": body.canonical_manager, "u": user.username}
    for field in ("include_in_rankings", "include_in_business_totals", "status", "note"):
        value = getattr(body, field)
        if value is not None:
            sets.append(f"{field} = %({field})s")
            params[field] = value
    if not sets:
        raise HTTPException(400, "nothing to change")
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(f"""UPDATE reporting_manager SET {', '.join(sets)},
                            updated_by = %(u)s, updated_at = now()
                            WHERE canonical_manager = %(m)s RETURNING canonical_manager""",
                        params)
            if cur.fetchone() is None:
                raise HTTPException(404, f"unknown manager '{body.canonical_manager}'")
    return {"canonical_manager": body.canonical_manager, "changed": list(params)}
