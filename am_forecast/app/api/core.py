"""API foundation.

Two rules run through this layer.

**No financial logic here.** Every monetary figure comes from a database view.
The API selects, filters, paginates and serialises; it never recomputes actual
income, forecasts, budgets, outlook, outcomes or achievement. If a number is
wrong, there is exactly one place to fix it.

**NULL survives the wire.** A measure that is unavailable is serialised as
``null`` with a reason, never coerced to zero, and the frontend renders it as
N/A. Turning an unavailable baseline into 0% would report a manager as having
failed when the truth is that we cannot say.
"""
from __future__ import annotations

import datetime as dt
import os
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Generic, Iterable, Sequence, TypeVar

import psycopg2
import psycopg2.extras
from fastapi import Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

DSN = os.environ.get("AM_FORECAST_DSN", "dbname=am_forecast")
TIMEZONE = "Australia/Melbourne"
GST_NOTE = "All income figures are GST inclusive."

T = TypeVar("T")

CENT = Decimal("0.01")


def to_cents(value) -> Decimal | None:
    """Round a monetary value to cents, half away from zero.

    Python's default is half-to-even, which disagrees with PostgreSQL's round()
    and with every accounting convention the business uses. Currency rounding is
    defined here once so the API, the exports and the database never differ by a
    cent on the same number.
    """
    if value is None:
        return None
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


# --- database ----------------------------------------------------------------

@contextmanager
def cursor(readonly: bool = True):
    conn = psycopg2.connect(DSN)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SET TIME ZONE '{TIMEZONE}'")
            yield cur
        if readonly:
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fetch_all(sql: str, params: dict | None = None) -> list[dict]:
    with cursor() as cur:
        cur.execute(sql, params or {})
        return [dict(r) for r in cur.fetchall()]


def fetch_one(sql: str, params: dict | None = None) -> dict | None:
    rows = fetch_all(sql, params)
    return rows[0] if rows else None


# --- roles -------------------------------------------------------------------

ROLES = ("viewer", "manager", "administrator")
_RANK = {r: i for i, r in enumerate(ROLES)}


@dataclass(frozen=True)
class User:
    username: str
    role: str

    def at_least(self, role: str) -> bool:
        return _RANK[self.role] >= _RANK[role]


def current_user(request: Request) -> User:
    """Identify the caller.

    Delegates to the session layer, so every endpoint that already depends on
    this became session-backed without being touched. The import is deferred to
    avoid a cycle: the auth module imports User and ROLES from here.
    """
    from .auth import session_user
    return session_user(request)


def require(role: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if not user.at_least(role):
            raise HTTPException(
                status_code=403,
                detail=f"{role} role required; caller is '{user.role}'")
        return user
    return dependency


require_manager = require("manager")
require_admin = require("administrator")


# --- response models ---------------------------------------------------------

class Money(BaseModel):
    """A monetary measure that may legitimately be unavailable.

    `value` of None means N/A and must never be rendered as 0. `reason` explains
    why, so the interface can say what is missing rather than showing a blank.
    """

    model_config = ConfigDict(ser_json_inf_nan="null")

    value: Decimal | None = None
    available: bool = True
    reason: str | None = None

    @classmethod
    def of(cls, value, reason_if_null: str | None = None) -> "Money":
        if value is None:
            return cls(value=None, available=False, reason=reason_if_null)
        return cls(value=Decimal(value), available=True)


class Ratio(BaseModel):
    """A percentage that may be unavailable. Same rule as Money."""

    value: Decimal | None = None
    available: bool = True
    reason: str | None = None

    @classmethod
    def of(cls, value, reason_if_null: str | None = None) -> "Ratio":
        if value is None:
            return cls(value=None, available=False, reason=reason_if_null)
        return cls(value=Decimal(value), available=True)


class Page(BaseModel, Generic[T]):
    items: list[Any]
    total: int
    limit: int
    offset: int
    gst_note: str = GST_NOTE


class Meta(BaseModel):
    """Context every financial page must carry."""

    cut_off_date: dt.date
    generated_at: dt.datetime
    timezone: str = TIMEZONE
    gst_note: str = GST_NOTE
    financial_year: int | None = None
    # The month the calendar is in. Every page banner showed the stored cut-off,
    # which has decided nothing since migration 0020 and read "July" throughout
    # August -- so the one date on every screen was the one date that was wrong.
    current_month: dt.date | None = None
    notes: list[str] = Field(default_factory=list)


def current_month() -> dt.date:
    """The month under way, in Melbourne."""
    return fetch_one("SELECT reporting_current_month() AS m")["m"]


def last_completed_month() -> dt.date:
    """The most recent month that has actually finished.

    Two different boundaries, and collapsing them into one is how August went
    missing and then how it came back wrong.

    "Has this month started?" governs whether income is shown. August has
    started, so August income belongs on the page the day it is imported.

    "Has this month finished?" governs whether anything is JUDGED -- a
    like-for-like comparison with last year, a budget-to-date, an achievement
    percentage. A part month measured against a whole month's target is the
    fault that has recurred through this system more than any other.

    The stored cut-off used to answer both, badly. These answer one each.
    """
    return fetch_one("""SELECT (reporting_current_month()
                                - INTERVAL '1 month')::date AS m""")["m"]


def current_financial_year() -> int:
    """The financial year we are actually in, from the calendar.

    Every endpoint defaulted to Query(2026) -- a literal that would have gone
    quietly wrong on 1 July 2027, returning last year's figures to anyone who did
    not pick a year by hand, with nothing on screen to say so.

    Derived in Australia/Melbourne through the same function the rest of the
    system uses, so the app rolls into a new financial year without anybody
    touching it.
    """
    return fetch_one("""SELECT au_financial_year(
        (now() AT TIME ZONE 'Australia/Melbourne')::date) AS fy""")["fy"]


def supplied_month_note(financial_year: int) -> list[str]:
    """Name any month held at manager grain rather than policy grain.

    Both callers hard-coded `if financial_year == 2026` with July named in the
    text. That is true today and silently wrong from 1 July 2027: the note would
    keep appearing on a year it does not describe, or vanish from a year it does.

    The distinction is already in the data -- a month established from supplied
    figures carries grain 'manager_month', one built from an extract carries
    'policy' -- so it is read rather than remembered.
    """
    rows = fetch_all("""
        SELECT DISTINCT forecast_month FROM original_forecast
        WHERE financial_year = %(fy)s AND grain <> 'policy'
        ORDER BY forecast_month""", {"fy": financial_year})
    if not rows:
        return []
    months = ", ".join(f"{r['forecast_month']:%B %Y}" for r in rows)
    return [f"{months} uses supplied per-manager forecast figures, held at "
            f"manager-month level rather than policy level. Policy-level renewal "
            f"detail is unavailable for it."]


def meta(financial_year: int | None = None, notes: Iterable[str] = ()) -> Meta:
    row = fetch_one("""SELECT cut_off_date, reporting_current_month() AS current_month
                       FROM reporting_settings WHERE id = 1""")
    return Meta(cut_off_date=row["cut_off_date"],
                current_month=row["current_month"],
                generated_at=dt.datetime.now(),
                financial_year=financial_year,
                notes=list(notes))


# --- filtering ---------------------------------------------------------------

@dataclass
class Filters:
    """The filter set shared by every reporting area.

    Rendered into SQL fragments here, once, so a drill-down cannot apply a
    subtly different filter from the summary that led to it. That is what makes
    "every summary reconciles to its drill-down" hold rather than being hoped
    for.
    """

    financial_year: int | None = None
    quarter: int | None = None
    month: dt.date | None = None
    manager: str | None = None
    policy_class: str | None = None
    underwriter: str | None = None
    category: str | None = None
    direction: str | None = None
    client: str | None = None
    policy_number: str | None = None
    forecast_status: str | None = None
    snapshot_id: int | None = None
    batch_id: int | None = None
    include_excluded: bool = False

    COLUMNS = {
        "financial_year": "financial_year",
        "quarter": "financial_quarter",
        "month": "period_month",
        "manager": "canonical_manager",
        "policy_class": "policy_class",
        "underwriter": "uw_code",
        "category": "category",
        "direction": "financial_direction",
        "client": "client_code",
        "policy_number": "policy_number",
    }

    def clauses(self, available: Sequence[str], month_column: str = "period_month"
                ) -> tuple[str, dict]:
        """Build a WHERE fragment using only the columns a view actually has."""
        parts, params = [], {}
        for name, column in self.COLUMNS.items():
            value = getattr(self, name)
            if value is None:
                continue
            if name == "month":
                column = month_column
            if column not in available:
                continue
            if name in ("client", "policy_number"):
                parts.append(f"upper({column}) LIKE %({name})s")
                params[name] = f"%{str(value).upper()}%"
            else:
                parts.append(f"{column} = %({name})s")
                params[name] = value
        if self.snapshot_id is not None and "snapshot_id" in available:
            parts.append("snapshot_id = %(snapshot_id)s")
            params["snapshot_id"] = self.snapshot_id
        if not self.include_excluded and "is_excluded" in available:
            parts.append("NOT is_excluded")
        where = (" WHERE " + " AND ".join(parts)) if parts else ""
        return where, params


def filters(
    financial_year: int | None = Query(None, ge=2000, le=2100),
    quarter: int | None = Query(None, ge=1, le=4),
    month: dt.date | None = None,
    manager: str | None = None,
    policy_class: str | None = None,
    underwriter: str | None = None,
    category: str | None = None,
    direction: str | None = Query(None, pattern="^(positive|negative|nil)$"),
    client: str | None = None,
    policy_number: str | None = None,
    forecast_status: str | None = None,
    snapshot_id: int | None = None,
    batch_id: int | None = None,
    include_excluded: bool = False,
) -> Filters:
    # Deliberately NOT defaulted here.
    #
    # Defaulting the year in this shared dependency looked tidier and broke the
    # export: it aliases only the first condition of the WHERE clause, so adding
    # a year condition ahead of the others left the next column unqualified and
    # ambiguous across a join. A default that changes the SHAPE of a query, not
    # just its values, does not belong in something this widely used.
    #
    # Endpoints that need a year default it themselves, where the effect is
    # visible in one place.
    return Filters(financial_year=financial_year, quarter=quarter, month=month,
                   manager=manager, policy_class=policy_class,
                   underwriter=underwriter, category=category, direction=direction,
                   client=client, policy_number=policy_number,
                   forecast_status=forecast_status, snapshot_id=snapshot_id,
                   batch_id=batch_id, include_excluded=include_excluded)


def columns_of(view: str) -> list[str]:
    rows = fetch_all("""SELECT column_name FROM information_schema.columns
                        WHERE table_schema='public' AND table_name=%(v)s""",
                     {"v": view})
    return [r["column_name"] for r in rows]


def paginate(sql: str, params: dict, limit: int, offset: int) -> tuple[list[dict], int]:
    total = fetch_one(f"SELECT count(*) AS n FROM ({sql}) q", params)["n"]
    rows = fetch_all(f"{sql} LIMIT {int(limit)} OFFSET {int(offset)}", params)
    return rows, total
