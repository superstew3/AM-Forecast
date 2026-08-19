"""Operational endpoints: review, data quality, uploads, budget, exports."""
from __future__ import annotations

import csv
import datetime as dt
import io
import shutil
import tempfile
from decimal import Decimal
from pathlib import Path

import psycopg2
from fastapi import (
    APIRouter, Body, Depends, File, HTTPException, Query, UploadFile,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..importers import AcceptError, accept, prepare, reject, rollback
from ..matching import apportion, manual_match, reject_match, run_matching
from ..validation import ZERO_EXPECTED_EXPLANATION
from .core import (
    current_financial_year,
    DSN, GST_NOTE, Filters, Money, columns_of, current_user, fetch_all, fetch_one,
    filters, meta, paginate, require_admin, require_manager,
)

router = APIRouter()


def _conn():
    return psycopg2.connect(DSN)


# --- matching review queue ----------------------------------------------------

# The queue separates work a human should act on from bulk artefacts that need
# explanation, not individual triage. Presenting 584 July timing artefacts and
# 8,071 out-of-scope prior-year renewals as ordinary errors would bury the real
# exceptions and train reviewers to ignore the queue.
ACTIONABLE = ("multiple_policies_for_transaction", "low_tier_requires_review",
              "class_conflict", "unmatched_forecast_policy")


@router.get("/review", tags=["review"])
def review_queue(kind: str = Query("actionable",
                                   pattern="^(actionable|timing|out_of_scope|all)$"),
                 limit: int = Query(100, le=1000), offset: int = 0,
                 user=Depends(current_user)):
    cut = fetch_one("""SELECT date_trunc('month', cut_off_date)::date AS m
                       FROM reporting_settings WHERE id=1""")["m"]
    if kind == "actionable":
        where = "WHERE status='pending' AND reason = ANY(%(reasons)s)"
        params = {"reasons": list(ACTIONABLE)}
    elif kind == "timing":
        where = ("WHERE status='pending' AND reason='unmatched_actual_renewal' "
                 "AND date_trunc('month', transaction_date)::date = %(cut)s")
        params = {"cut": cut}
    elif kind == "out_of_scope":
        where = ("WHERE status='pending' AND reason='unmatched_actual_renewal' "
                 "AND date_trunc('month', transaction_date)::date <> %(cut)s")
        params = {"cut": cut}
    else:
        where, params = "WHERE status='pending'", {}
    rows, total = paginate(f"""
        SELECT id, reason, status, tier, confidence, candidate_rank, transaction_id,
               txn_client, txn_policy_number, txn_policy_class, txn_category,
               transaction_date, txn_income, policy_id, policy_client,
               policy_policy_number, policy_class, expiry_date, forecast_contribution,
               detail
        FROM v_match_review_queue {where} ORDER BY transaction_id, candidate_rank""",
        params, limit, offset)

    counts = fetch_one("""
        SELECT
          count(*) FILTER (WHERE status='pending' AND reason = ANY(%(reasons)s))
              AS actionable,
          count(*) FILTER (WHERE status='pending' AND reason='unmatched_actual_renewal'
                             AND date_trunc('month', transaction_date)::date = %(cut)s)
              AS july_timing_artefacts,
          count(*) FILTER (WHERE status='pending' AND reason='unmatched_actual_renewal'
                             AND date_trunc('month', transaction_date)::date <> %(cut)s)
              AS out_of_scope
        FROM v_match_review_queue""", {"reasons": list(ACTIONABLE), "cut": cut})

    return {"items": rows, "total": total, "limit": limit, "offset": offset,
            "counts": counts,
            "explanations": {
                "actionable": "Candidates a reviewer should decide.",
                "july_timing_artefacts":
                    "July 2026 renewals with no forecast policy to match. The pending "
                    "file was extracted after most July renewals had transacted, so "
                    "there is nothing to match them against. Not individual errors.",
                "out_of_scope":
                    "Renewal transactions in months with no policy-grain forecast, "
                    "chiefly FY2025-26. There was never a forecast to match them to."},
            "meta": meta(), "gst_note": GST_NOTE}


class MatchDecision(BaseModel):
    policy_id: int | None = None
    forecast_month: dt.date | None = None
    transaction_id: int
    reason: str = Field(min_length=3)
    allocated_income: Decimal | None = None
    splits: list[dict] | None = None


@router.post("/review/match", tags=["review"])
def do_manual_match(body: MatchDecision, user=Depends(require_admin)):
    if body.policy_id is None or body.forecast_month is None:
        raise HTTPException(400, "policy_id and forecast_month are required")
    with _conn() as conn:
        return manual_match(conn, body.policy_id, body.forecast_month,
                            body.transaction_id, user.username, body.reason,
                            body.allocated_income)


@router.post("/review/reject", tags=["review"])
def do_reject_match(body: MatchDecision, user=Depends(require_admin)):
    with _conn() as conn:
        return reject_match(conn, body.transaction_id, user.username, body.reason,
                            body.policy_id)


@router.post("/review/apportion", tags=["review"])
def do_apportion(body: MatchDecision, user=Depends(require_admin)):
    if not body.splits:
        raise HTTPException(400, "splits are required")
    parsed = [(int(s["policy_id"]), dt.date.fromisoformat(str(s["forecast_month"])),
               Decimal(str(s["allocated_income"]))) for s in body.splits]
    try:
        with _conn() as conn:
            return apportion(conn, body.transaction_id, parsed, user.username, body.reason)
    except psycopg2.errors.RaiseException as exc:
        raise HTTPException(400, str(exc).splitlines()[0])


@router.post("/review/rematch", tags=["review"])
def do_rematch(user=Depends(require_admin)):
    """Re-run automatic matching. Manual allocations and their audit survive."""
    with _conn() as conn:
        return run_matching(conn, user.username).__dict__


@router.get("/review/history", tags=["review"])
def decision_history(limit: int = Query(100, le=1000), offset: int = 0,
                     policy_id: int | None = None, user=Depends(current_user)):
    where, params = "", {}
    if policy_id:
        where, params = " WHERE policy_id = %(policy_id)s", {"policy_id": policy_id}
    rows, total = paginate(f"""
        SELECT id, decided_at, reviewer, action, reason, policy_id, forecast_month,
               transaction_id, previous_decision, new_decision, client_code,
               policy_number, category, actual_income
        FROM v_match_decision_history{where}""", params, limit, offset)
    return {"items": rows, "total": total, "meta": meta()}


# --- data quality -------------------------------------------------------------

@router.get("/data-quality", tags=["data-quality"])
def data_quality(user=Depends(current_user)):
    counts = fetch_one("""
        SELECT
          (SELECT count(*) FROM forecast_policy
            WHERE NOT is_excluded AND 'negative_expected' = ANY(exception_flags))
              AS negative_expected_policies,
          (SELECT count(*) FROM forecast_policy
            WHERE NOT is_excluded AND 'zero_expected' = ANY(exception_flags))
              AS zero_expected_policies,
          (SELECT count(*) FROM forecast_policy
            WHERE NOT is_excluded AND 'overdue_pending' = ANY(exception_flags))
              AS overdue_pending_policies,
          (SELECT count(*) FROM forecast_policy
            WHERE NOT is_excluded AND 'residual_pending' = ANY(exception_flags))
              AS residual_pending_policies,
          (SELECT count(DISTINCT source_manager) FROM v_sales_reported
            WHERE canonical_manager IS NULL) AS unmapped_managers,
          (SELECT count(*) FROM sales_transaction
            WHERE NOT is_excluded AND business_classification = 'Unmapped')
              AS unmapped_categories,
          (SELECT count(*) FROM (
              SELECT DISTINCT upper(trim(class_abbrev)) v FROM forecast_policy
               WHERE NOT is_excluded
              EXCEPT SELECT source_value FROM class_equivalence
               WHERE source_type='renewals') x) AS unmapped_class_equivalences,
          (SELECT count(*) FROM restated_transaction WHERE resolved_at IS NULL)
              AS restated_transactions,
          (SELECT count(*) FROM match_candidate
            WHERE status='pending' AND reason='multiple_policies_for_transaction')
              AS ambiguous_matches,
          (SELECT count(*) FROM v_allocation_breaches) AS allocation_breaches,
          (SELECT count(*) FROM forecast_baseline
            WHERE baseline_status <> 'complete' OR suppress_achievement)
              AS unavailable_baselines,
          (SELECT count(*) FROM period_coverage WHERE coverage_status='partial')
              AS partial_financial_years,
          (SELECT count(*) FROM sales_transaction WHERE is_excluded)
              AS excluded_sales_records,
          (SELECT count(*) FROM forecast_policy WHERE is_excluded)
              AS excluded_forecast_records
    """)
    return {
        "counts": counts,
        # Counted from the data rather than asserted against a figure fixed to
        # one export. The indicator's job is to surface these policies, not to
        # confirm a number that stopped being true when new data arrived.
        # The keys carry the "_policies" suffix; without it these silently
        # returned zero and the indicator claimed to expect nothing.
        "expected": {"zero_expected_policies": counts.get("zero_expected_policies", 0),
                     "negative_expected_policies":
                         counts.get("negative_expected_policies", 0)},
        "notes": {"zero_expected_policies": ZERO_EXPECTED_EXPLANATION},
        "partial_periods": fetch_all("""SELECT financial_year, data_domain,
                                               coverage_status, months_present, label
                                        FROM period_coverage
                                        WHERE coverage_status='partial'
                                        ORDER BY financial_year"""),
        "baselines": fetch_all("""SELECT forecast_month, baseline_status,
                                         baseline_source, suppress_achievement,
                                         manager_exceptions, note
                                  FROM forecast_baseline
                                  ORDER BY forecast_month"""),
        "meta": meta(), "gst_note": GST_NOTE}


@router.get("/data-quality/{indicator}", tags=["data-quality"])
def data_quality_detail(indicator: str, limit: int = Query(200, le=2000), offset: int = 0,
                        user=Depends(current_user)):
    """Drill-down behind each indicator."""
    flag_map = {"zero_expected_policies": "zero_expected",
                "negative_expected_policies": "negative_expected",
                "overdue_pending_policies": "overdue_pending",
                "residual_pending_policies": "residual_pending"}
    if indicator in flag_map:
        rows, total = paginate("""
            SELECT policy_id, client_code, policy_number, class_abbrev,
                   underwriter_abbrev, expiry_date, source_manager,
                   comm, comm_tax, fee, fee_tax, raw_expected_income,
                   forecast_contribution, exception_flags
            FROM forecast_policy
            WHERE NOT is_excluded AND %(flag)s = ANY(exception_flags)
            ORDER BY policy_id""", {"flag": flag_map[indicator]}, limit, offset)
    elif indicator == "excluded_records":
        rows, total = paginate("""
            SELECT 'sales' AS source, id::text AS record, source_manager,
                   exclusion_field, exclusion_value, category,
                   actual_income AS amount, transaction_date::date AS record_date
            FROM sales_transaction WHERE is_excluded
            UNION ALL
            SELECT 'renewals', policy_id::text, source_manager, exclusion_field,
                   exclusion_value, class_abbrev, forecast_contribution, expiry_date
            FROM forecast_policy WHERE is_excluded
            ORDER BY source, record_date DESC""", {}, limit, offset)
    elif indicator == "allocation_breaches":
        rows, total = paginate("SELECT * FROM v_allocation_breaches", {}, limit, offset)
    elif indicator == "restated_transactions":
        rows, total = paginate("""
            SELECT r.id, r.transaction_id, r.batch_id, r.changed_fields, r.detected_at,
                   t.client_code, t.policy_number, t.category
            FROM restated_transaction r
            JOIN sales_transaction t ON t.id = r.transaction_id
            WHERE r.resolved_at IS NULL ORDER BY r.id""", {}, limit, offset)
    else:
        raise HTTPException(404, f"unknown indicator '{indicator}'")
    return {"indicator": indicator, "items": rows, "total": total,
            "limit": limit, "offset": offset, "meta": meta()}


# --- uploads ------------------------------------------------------------------

@router.get("/uploads", tags=["uploads"])
def uploads(limit: int = Query(50, le=500), offset: int = 0, user=Depends(current_user)):
    rows, total = paginate("""
        SELECT b.id, b.file_name, b.file_type, b.file_sha256, b.file_size_bytes,
               b.uploaded_by, b.uploaded_at, b.accepted_by, b.accepted_at, b.status,
               b.source_row_count, b.accepted_row_count, b.duplicate_row_count,
               b.excluded_row_count, b.rejected_row_count, b.exception_count,
               b.coverage_start, b.coverage_end, b.positive_income, b.return_income,
               b.net_income, b.expected_forecast_income, b.validation_messages,
               b.requires_confirmation, b.confirmed_by, b.confirmed_at,
               b.confirmed_months, b.coverage_warnings,
               b.rolled_back_by, b.rolled_back_at, b.rollback_reason,
               (SELECT row_to_json(r) FROM batch_rollback r
                 WHERE r.batch_id = b.id ORDER BY r.id DESC LIMIT 1) AS rollback_detail
        FROM upload_batch b ORDER BY b.id DESC""", {}, limit, offset)
    return {"items": rows, "total": total, "meta": meta(), "gst_note": GST_NOTE}


@router.post("/uploads/prepare", tags=["uploads"])
async def upload_prepare(file: UploadFile = File(...), user=Depends(require_admin)):
    """Stage and preview a file. Touches no fact table.

    The summary returned here is computed from the staged rows, so it is exactly
    what will land on accept.
    """
    tmp = Path(tempfile.mkdtemp()) / (file.filename or "upload.csv")
    with tmp.open("wb") as fh:
        shutil.copyfileobj(file.file, fh)
    try:
        with _conn() as conn:
            s = prepare(conn, str(tmp), user.username)
        coverage = None
        if s.coverage is not None:
            coverage = {
                "months": [vars(m) for m in s.coverage.months],
                "absent_months": [vars(m) for m in s.coverage.absent_months],
                "requires_confirmation": s.coverage.requires_confirmation,
                "warnings": s.coverage.warnings,
            }
        return {"batch_id": s.batch_id, "file_name": s.file_name,
                "file_type": s.file_type, "label": s.label,
                "detection_confidence": s.detection_confidence,
                "source_rows": s.source_rows, "valid_rows": s.valid_rows,
                "duplicate_rows": s.duplicate_rows, "excluded_rows": s.excluded_rows,
                "rejected_rows": s.rejected_rows, "restated_rows": s.restated_rows,
                "positive_income": s.positive_income, "return_income": s.return_income,
                "net_income": s.net_income,
                "raw_expected_income": s.raw_expected_income,
                "forecast_contribution": s.forecast_contribution,
                "coverage_start": s.coverage_start, "coverage_end": s.coverage_end,
                "exception_count": s.exception_count,
                "exceptions_by_type": s.exceptions_by_type,
                "messages": s.messages,
                "requires_confirmation": s.requires_confirmation,
                "coverage": coverage,
                "rendered": s.render(), "gst_note": GST_NOTE}
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)


class AcceptBody(BaseModel):
    confirmed_months: list[dt.date] | None = None
    force: bool = False


@router.post("/uploads/{batch_id}/accept", tags=["uploads"])
def upload_accept(batch_id: int, body: AcceptBody = Body(default=AcceptBody()),
                  user=Depends(require_admin)):
    try:
        with _conn() as conn:
            return accept(conn, batch_id, user.username, force=body.force,
                          confirmed_months=body.confirmed_months)
    except AcceptError as exc:
        raise HTTPException(409, str(exc))


class ReasonBody(BaseModel):
    reason: str = Field(min_length=3)
    force: bool = False


@router.post("/uploads/{batch_id}/reject", tags=["uploads"])
def upload_reject(batch_id: int, body: ReasonBody, user=Depends(require_admin)):
    try:
        with _conn() as conn:
            return reject(conn, batch_id, body.reason, user.username)
    except AcceptError as exc:
        raise HTTPException(409, str(exc))


@router.post("/uploads/{batch_id}/rollback", tags=["uploads"])
def upload_rollback(batch_id: int, body: ReasonBody, user=Depends(require_admin)):
    try:
        with _conn() as conn:
            return rollback(conn, batch_id, body.reason, user.username, force=body.force)
    except Exception as exc:
        raise HTTPException(409, str(exc))


# --- budget -------------------------------------------------------------------

@router.get("/budget", tags=["budget"])
def budget(financial_year: int | None = Query(None), user=Depends(current_user)):
    """Budget with the active assumption and where in the hierarchy it came from."""
    financial_year = financial_year or current_financial_year()
    quarters = fetch_all("""
        SELECT canonical_manager, financial_quarter, original_renewal_forecast,
               growth_basis, growth_pct, dollar_override, new_business_growth_target,
               total_budget, has_locked_months, locked_months
        FROM v_budget_quarter WHERE financial_year=%(fy)s
        ORDER BY canonical_manager, financial_quarter""", {"fy": financial_year})
    monthly = fetch_all("""
        SELECT canonical_manager, forecast_month, financial_quarter, original_forecast,
               growth_basis, growth_pct, allocation_method, calculated_growth_target,
               override_amount, new_business_growth_target, is_overridden,
               override_reason, total_budget, is_locked, locked_at, locked_by,
               lock_reason
        FROM v_monthly_budget WHERE financial_year=%(fy)s
        ORDER BY canonical_manager, forecast_month""", {"fy": financial_year})
    rates = fetch_all("""
        SELECT id, scope, canonical_manager, financial_year, financial_quarter,
               growth_pct, dollar_override, note, created_by, created_at
        FROM growth_rate WHERE active ORDER BY scope, canonical_manager""")
    return {"quarters": quarters, "monthly": monthly, "active_rates": rates,
            "meta": meta(financial_year, notes=[
                "Total Budget = Original Renewal Forecast + New Business Growth Target. "
                "It does not move when the Latest Forecast moves, a policy lapses or a "
                "cancellation returns income.",
                "The quarterly growth target is allocated across months by each month's "
                "share of that quarter's Original Renewal Forecast, not in equal thirds."]),
            "gst_note": GST_NOTE}


class GrowthBody(BaseModel):
    scope: str = Field(pattern="^(global|manager|manager_quarter|manager_month)$")
    canonical_manager: str | None = None
    financial_year: int | None = None
    financial_quarter: int | None = None
    target_month: dt.date | None = None
    growth_pct: Decimal | None = None
    dollar_override: Decimal | None = None
    reason: str = Field(min_length=3)


@router.post("/budget/growth-rate", tags=["budget"])
def set_growth_rate(body: GrowthBody, user=Depends(require_admin)):
    if body.growth_pct is None and body.dollar_override is None:
        raise HTTPException(400, "growth_pct or dollar_override is required")

    # Naming a manager while the scope is global is contradictory, and the
    # dangerous reading is the silent one: the manager is ignored and every
    # manager's budget moves. Refuse rather than guess.
    if body.scope == "global" and body.canonical_manager:
        raise HTTPException(
            400,
            f"scope is 'global' but '{body.canonical_manager}' was named. A global "
            "rate applies to every manager. Use scope 'manager' to change one "
            "manager, or clear the manager to change everyone.")
    if body.scope != "global" and not body.canonical_manager:
        raise HTTPException(
            400, f"scope '{body.scope}' requires a manager.")
    if body.scope == "manager_quarter" and not (body.financial_year
                                                and body.financial_quarter):
        raise HTTPException(
            400, "scope 'manager_quarter' requires financial_year and "
                 "financial_quarter.")
    if body.scope == "manager_month" and not body.target_month:
        raise HTTPException(400, "scope 'manager_month' requires target_month.")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT id, growth_pct, dollar_override FROM growth_rate
                           WHERE active AND scope=%s
                             AND canonical_manager IS NOT DISTINCT FROM %s
                             AND financial_year IS NOT DISTINCT FROM %s
                             AND financial_quarter IS NOT DISTINCT FROM %s
                             AND target_month IS NOT DISTINCT FROM %s""",
                        (body.scope, body.canonical_manager, body.financial_year,
                         body.financial_quarter, body.target_month))
            previous = cur.fetchone()
            if previous:
                cur.execute("""UPDATE growth_rate SET active=false, superseded_at=now()
                               WHERE id=%s""", (previous[0],))
            cur.execute("""
                INSERT INTO growth_rate (scope, canonical_manager, financial_year,
                    financial_quarter, target_month, growth_pct, dollar_override,
                    note, active, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,true,%s) RETURNING id""",
                        (body.scope, body.canonical_manager, body.financial_year,
                         body.financial_quarter, body.target_month, body.growth_pct,
                         body.dollar_override, body.reason, user.username))
            new_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO budget_audit (action, scope_description, canonical_manager,
                    financial_year, financial_quarter, before_value, after_value,
                    reason, performed_by)
                VALUES ('set_growth_rate', %s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s, %s)""",
                        (f"{body.scope} growth rate", body.canonical_manager,
                         body.financial_year, body.financial_quarter,
                         psycopg2.extras.Json({"growth_pct": str(previous[1]),
                                               "dollar_override": str(previous[2])})
                         if previous else None,
                         psycopg2.extras.Json({"growth_pct": str(body.growth_pct),
                                               "dollar_override": str(body.dollar_override)}),
                         body.reason, user.username))
    return {"id": new_id, "scope": body.scope, "replaced": previous[0] if previous else None}


class MonthlyOverrideBody(BaseModel):
    canonical_manager: str
    target_month: dt.date
    override_amount: Decimal
    reason: str = Field(min_length=3)


@router.post("/budget/monthly-override", tags=["budget"])
def set_monthly_override(body: MonthlyOverrideBody, user=Depends(require_admin)):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT override_amount FROM monthly_target_override
                           WHERE canonical_manager=%s AND target_month=%s AND active""",
                        (body.canonical_manager, body.target_month))
            previous = cur.fetchone()
            cur.execute("""UPDATE monthly_target_override SET active=false
                           WHERE canonical_manager=%s AND target_month=%s AND active""",
                        (body.canonical_manager, body.target_month))
            cur.execute("""
                INSERT INTO monthly_target_override
                    (canonical_manager, target_month, override_amount, reason,
                     created_by, active)
                VALUES (%s,%s,%s,%s,%s,true) RETURNING id""",
                        (body.canonical_manager, body.target_month, body.override_amount,
                         body.reason, user.username))
            new_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO budget_audit (action, scope_description, canonical_manager,
                    before_value, after_value, reason, performed_by)
                VALUES ('set_monthly_override', %s, %s, %s::jsonb, %s::jsonb, %s, %s)""",
                        (f"monthly override {body.target_month}", body.canonical_manager,
                         psycopg2.extras.Json({"override_amount": str(previous[0])})
                         if previous else None,
                         psycopg2.extras.Json({"override_amount": str(body.override_amount)}),
                         body.reason, user.username))
    return {"id": new_id}


class LockBody(BaseModel):
    canonical_manager: str
    target_month: dt.date
    reason: str = Field(min_length=3)


@router.post("/budget/lock", tags=["budget"])
def lock_budget_month(body: LockBody, user=Depends(require_admin)):
    """Freeze a month's budget at the figure it currently holds.

    Once a target has been agreed with a manager it should not drift because a
    later Renewals Pending upload moved the forecast underneath it. Locking
    stores the whole budget and its components as at this moment; unlocking is a
    separate, audited act.
    """
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT original_forecast, new_business_growth_target, total_budget,
                       growth_pct, is_locked
                FROM v_monthly_budget
                WHERE canonical_manager = %s AND forecast_month = %s""",
                        (body.canonical_manager, body.target_month))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(
                    404, f"no budget for {body.canonical_manager} in "
                         f"{body.target_month:%B %Y}")
            forecast, target, budget, growth_pct, already = row
            if already:
                raise HTTPException(409, "that month is already locked")

            cur.execute("""
                INSERT INTO budget_lock
                    (canonical_manager, target_month, locked_budget,
                     locked_renewal_forecast, locked_growth_target, locked_growth_pct,
                     reason, locked_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (body.canonical_manager, body.target_month, budget, forecast,
                         target, growth_pct, body.reason, user.username))
            lock_id = cur.fetchone()[0]
            cur.execute("""
                INSERT INTO budget_audit (action, scope_description, canonical_manager,
                    before_value, after_value, reason, performed_by)
                VALUES ('lock_budget_month', %s, %s, NULL, %s::jsonb, %s, %s)""",
                        (f"budget lock {body.target_month}", body.canonical_manager,
                         psycopg2.extras.Json({"locked_budget": str(budget),
                                               "renewal_forecast": str(forecast),
                                               "growth_target": str(target)}),
                         body.reason, user.username))
    return {"id": lock_id, "canonical_manager": body.canonical_manager,
            "target_month": body.target_month, "locked_budget": budget,
            "note": "This month's budget will not move if the forecast changes."}


@router.post("/budget/unlock", tags=["budget"])
def unlock_budget_month(body: LockBody, user=Depends(require_admin)):
    """Release a locked month so it follows the forecast again."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE budget_lock SET active=false, unlocked_by=%s, unlocked_at=now(),
                       unlock_reason=%s
                WHERE canonical_manager=%s AND target_month=%s AND active
                RETURNING locked_budget""",
                        (user.username, body.reason, body.canonical_manager,
                         body.target_month))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(404, "that month is not locked")
            cur.execute("""
                INSERT INTO budget_audit (action, scope_description, canonical_manager,
                    before_value, after_value, reason, performed_by)
                VALUES ('unlock_budget_month', %s, %s, %s::jsonb, NULL, %s, %s)""",
                        (f"budget unlock {body.target_month}", body.canonical_manager,
                         psycopg2.extras.Json({"locked_budget": str(row[0])}),
                         body.reason, user.username))
    return {"canonical_manager": body.canonical_manager,
            "target_month": body.target_month, "released_from": row[0]}


@router.get("/budget/locks", tags=["budget"])
def budget_locks(user=Depends(current_user)):
    return {"items": fetch_all("""
        SELECT id, canonical_manager, target_month, locked_budget,
               locked_renewal_forecast, locked_growth_target, locked_growth_pct,
               reason, locked_by, locked_at
        FROM budget_lock WHERE active
        ORDER BY canonical_manager, target_month"""),
        "history": fetch_all("""
        SELECT canonical_manager, target_month, locked_budget, locked_by, locked_at,
               unlocked_by, unlocked_at, unlock_reason
        FROM budget_lock WHERE NOT active ORDER BY unlocked_at DESC LIMIT 50"""),
        "meta": meta()}


@router.get("/budget/audit", tags=["budget"])
def budget_audit(limit: int = Query(100, le=1000), offset: int = 0,
                 user=Depends(current_user)):
    rows, total = paginate("""
        SELECT id, action, scope_description, canonical_manager, financial_year,
               financial_quarter, before_value, after_value, reason, performed_by,
               performed_at
        FROM budget_audit ORDER BY id DESC""", {}, limit, offset)
    return {"items": rows, "total": total, "meta": meta()}


# --- exports ------------------------------------------------------------------

EXPORTS = {
    "managers": ("Account manager performance", """
        SELECT a.canonical_manager, a.financial_year, a.financial_quarter,
               a.period_month, a.positive_actual_income, a.absolute_return_income,
               a.net_actual_income, a.actual_renewal_income, a.actual_new_business,
               f.original_forecast, f.latest_forecast,
               b.new_business_growth_target, b.total_budget,
               r.renewal_income, r.renewal_achievement
        FROM v_actual_month a
        LEFT JOIN v_forecast_position_month f
               ON f.canonical_manager = a.canonical_manager
              AND f.forecast_month = a.period_month
        LEFT JOIN v_monthly_budget b
               ON b.canonical_manager = a.canonical_manager
              AND b.forecast_month = a.period_month
        LEFT JOIN v_renewal_income_month r
               ON r.canonical_manager = a.canonical_manager
              AND r.period_month = a.period_month"""),
    "policies": ("Policy-level renewals", """
        SELECT policy_id, forecast_month, client_code, policy_number, class_abbrev,
               underwriter_abbrev, expiry_date, original_manager, canonical_manager,
               original_forecast_income, latest_forecast_income, forecast_movement,
               renewal_transaction_income, total_associated_income, outcome,
               best_tier, confidence, requires_review, exception_flags
        FROM v_policy_renewal"""),
    "forecast-movement": ("Forecast movement", """
        SELECT policy_id, forecast_month, movement_type, secondary_changes,
               added, removed, amount_changed, manager_changed, detail_changed,
               original_income, previous_income, latest_income, movement_amount,
               canonical_from_manager, canonical_to_manager, client_code,
               policy_number, class_abbrev, expiry_date
        FROM v_forecast_movement_detail"""),
    "return-income": ("Return income", """
        SELECT canonical_manager, financial_year, financial_quarter, period_month,
               derived_classification, signed_return_income, absolute_return_income,
               transaction_rows
        FROM v_return_income_analysis"""),
    "transactions": ("Transactions", """
        SELECT id, transaction_date, period_month, financial_year, financial_quarter,
               source_manager, canonical_manager, client_code, policy_number,
               invoice_number, category, business_classification,
               derived_classification, policy_class, uw_code, commission, fees,
               actual_income, positive_income, signed_return_income,
               financial_direction
        FROM v_sales_reported"""),
}

# Measure classification, so an exported column can never be mistaken for a
# different kind of number.
MEASURE_KIND = {
    "original_forecast_income": "Original Forecast",
    "original_forecast": "Original Forecast",
    "original_renewal_forecast": "Original Forecast",
    "latest_forecast_income": "Latest Forecast",
    "latest_forecast": "Latest Forecast",
    "latest_income": "Latest Forecast",
    "previous_income": "Latest Forecast (previous snapshot)",
    "movement_amount": "Forecast Movement",
    "forecast_movement": "Forecast Movement",
    "total_budget": "Budget",
    "new_business_growth_target": "Budget",
    "latest_outlook": "Outlook",
    "remaining_budget_gap": "Outlook",
}


def _classify(column: str) -> str:
    if column in MEASURE_KIND:
        return MEASURE_KIND[column]
    if any(k in column for k in ("actual", "income", "commission", "fees")):
        return "Actual"
    return ""


@router.get("/export/{dataset}", tags=["export"])
def export(dataset: str, fmt: str = Query("csv", pattern="^(csv|xlsx)$"),
           f: Filters = Depends(filters), user=Depends(current_user)):
    """Export the filtered dataset.

    The export applies the same filters as the screen, keeps full unrounded
    values, writes N/A rather than blank or zero for unavailable measures, and
    carries the cut-off date, report date, GST statement and a measure-kind
    header row.
    """
    if dataset not in EXPORTS:
        raise HTTPException(404, f"unknown dataset '{dataset}'")
    title, base_sql = EXPORTS[dataset]
    if base_sql is None:
        raise HTTPException(400, "use /export/policies or another dataset")
    # Filter against the driving view's columns. Joined exports name their
    # driving view first, so the split takes that rather than the last join.
    view = base_sql.strip().split("FROM ")[1].split()[0].strip()
    where, params = f.clauses(columns_of(view))
    if where:
        where = where.replace(" WHERE ", " WHERE a.", 1) if view.startswith("v_actual") \
            else where
    rows = fetch_all(f"{base_sql}{where}", params)
    settings = fetch_one("SELECT cut_off_date FROM reporting_settings WHERE id=1")

    columns = list(rows[0].keys()) if rows else []
    preamble = [
        [f"Account Manager Income Forecasting - {title}"],
        [GST_NOTE],
        [f"Reporting cut-off date: {settings['cut_off_date']}"],
        [f"Report generated: {dt.datetime.now().isoformat(timespec='seconds')}"],
        [f"Timezone: Australia/Melbourne"],
        [f"Filters: {f}"],
        ["Unavailable measures are shown as N/A and are not the same as zero."],
        [],
    ]

    def cell(value):
        return "N/A" if value is None else value

    if fmt == "csv":
        buf = io.StringIO()
        w = csv.writer(buf)
        for line in preamble:
            w.writerow(line)
        w.writerow([_classify(c) for c in columns])
        w.writerow(columns)
        for r in rows:
            w.writerow([cell(r[c]) for c in columns])
        buf.seek(0)
        return StreamingResponse(
            iter([buf.getvalue()]), media_type="text/csv",
            headers={"Content-Disposition":
                     f'attachment; filename="{dataset}_{settings["cut_off_date"]}.csv"'})

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    for line in preamble:
        ws.append(line)
    ws.append([_classify(c) for c in columns])
    ws.append(columns)
    for r in rows:
        ws.append([cell(r[c]) for c in columns])
    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return StreamingResponse(
        out, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f'attachment; filename="{dataset}_{settings["cut_off_date"]}.xlsx"'})
