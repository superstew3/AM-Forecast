"""Administrator override for a locked forecast month.

A forecast upload writes only months that have not started, in Melbourne time.
That is the right default and it needs no thought: upload whenever, and only the
future moves.

But defaults have to be overridable, or people work around them. A month may
have begun before anyone got to the export; a target may have been set from a
file later found wrong; a new manager may need a figure for a month already
under way. Refusing outright would send that work into spreadsheets, where it is
neither visible nor auditable.

So an administrator can open one month, once. It is deliberate rather than
convenient, and it leaves a record: who opened it, why, what the month held
before and after, and which upload consumed it.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import psycopg2

from .core import (
    DSN, current_user, fetch_all, fetch_one, meta, require_admin,
)

router = APIRouter(prefix="/forecast-months", tags=["forecast months"])


class OverrideRequest(BaseModel):
    forecast_month: dt.date = Field(
        description="Any date in the month to open; it is truncated to the 1st.")
    reason: str = Field(min_length=8, description=(
        "Why this month is being reopened. Recorded permanently and shown "
        "beside the figure it produces, so write it for whoever reads the "
        "number in six months, not for the form."))


@router.get("/status")
def month_status(user=Depends(current_user)):
    """Every month the system knows about, and whether an upload can write it."""
    return {
        "current_month": fetch_one("SELECT reporting_current_month() AS m")["m"],
        "months": fetch_all("""
            SELECT m.forecast_month,
                   month_state(m.forecast_month)            AS state,
                   forecast_month_is_open(m.forecast_month)  AS open_to_upload,
                   forecast_month_writable(m.forecast_month) AS writable_now,
                   EXISTS (SELECT 1 FROM forecast_month_override o
                           WHERE o.forecast_month = m.forecast_month
                             AND o.consumed_at IS NULL)      AS override_pending,
                   COALESCE(SUM(m.forecast_contribution), 0) AS forecast_income
            FROM (SELECT forecast_month, forecast_contribution
                  FROM original_forecast) m
            GROUP BY 1 ORDER BY 1"""),
        "meta": meta(),
    }


@router.post("/override")
def grant_override(body: OverrideRequest,
                   user=Depends(require_admin)):
    """Open one closed month for the next upload that covers it.

    Deliberately single-use. A standing exemption would quietly become the rule,
    and the month would drift every time somebody uploaded a file — which is the
    behaviour the lock exists to stop.
    """
    month = body.forecast_month.replace(day=1)

    current = fetch_one("SELECT reporting_current_month() AS m")["m"]
    if month > current:
        raise HTTPException(400, (
            f"{month:%B %Y} has not started, so an upload can already write it. "
            f"No override is needed."))

    existing = fetch_one("""SELECT id, granted_by, granted_at, reason
                            FROM forecast_month_override
                            WHERE forecast_month = %s AND consumed_at IS NULL""",
                         (month,))
    if existing:
        raise HTTPException(409, (
            f"{month:%B %Y} is already open, granted by {existing['granted_by']} "
            f"at {existing['granted_at']:%d %b %H:%M}: {existing['reason']}"))

    before = fetch_one("""SELECT COALESCE(SUM(forecast_contribution), 0) AS total,
                                 count(*) AS policies
                          FROM original_forecast WHERE forecast_month = %s""",
                       (month,))

    # Own connection and an explicit commit: fetch_one is a read helper and does
    # not commit, so an INSERT through it returns a row and then discards it. The
    # endpoint answered 200 while writing nothing.
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO forecast_month_override
                             (forecast_month, granted_by, reason, before_total)
                           VALUES (%s, %s, %s, %s)
                           RETURNING id, granted_at""",
                        (month, user.username, body.reason, before["total"]))
            override_id, granted_at = cur.fetchone()
        conn.commit()

    return {
        "granted": True,
        "forecast_month": month,
        "override_id": override_id,
        "granted_at": granted_at,
        "current_figure": before["total"],
        "current_policies": before["policies"],
        "note": (f"{month:%B %Y} is open for the next upload that covers it. "
                 f"It closes again automatically once used. Nothing has changed "
                 f"yet — the figure moves when the file is accepted."),
        "meta": meta(),
    }


@router.delete("/override/{month}")
def revoke_override(month: dt.date,
                    user=Depends(require_admin)):
    """Withdraw an override that has not been used."""
    month = month.replace(day=1)
    with psycopg2.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("""DELETE FROM forecast_month_override
                           WHERE forecast_month = %s AND consumed_at IS NULL
                           RETURNING id""", (month,))
            row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(404, f"No open override for {month:%B %Y}.")
    return {"revoked": True, "forecast_month": month, "meta": meta()}


@router.get("/override/history")
def override_history(user=Depends(current_user)):
    """Every override ever granted, used or not.

    Kept after use rather than deleted. A figure that differs from what a routine
    upload would have produced needs to stay answerable, and "who changed this
    and why" is the question that gets asked months later.
    """
    return {
        "overrides": fetch_all("""
            SELECT o.id, o.forecast_month, o.granted_by, o.granted_at, o.reason,
                   o.consumed_at, o.consumed_batch_id,
                   o.before_total, o.after_total,
                   b.file_name AS consumed_file
            FROM forecast_month_override o
            LEFT JOIN upload_batch b ON b.id = o.consumed_batch_id
            ORDER BY o.granted_at DESC"""),
        "meta": meta(),
    }
