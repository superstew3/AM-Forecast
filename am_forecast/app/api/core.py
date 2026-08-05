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
from fastapi import Depends, Header, HTTPException, Query
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


def current_user(
    x_user: str = Header(default="viewer", alias="X-User"),
    x_role: str = Header(default="viewer", alias="X-Role"),
) -> User:
    """Identify the caller.

    Header-based so the app can sit behind whatever the business already uses
    for authentication. Swapping in SSO means replacing this one dependency;
    nothing downstream changes.
    """
    role = (x_role or "viewer").lower()
    if role not in ROLES:
        raise HTTPException(status_code=403, detail=f"unknown role '{x_role}'")
    return User(username=x_user or "unknown", role=role)


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
    notes: list[str] = Field(default_factory=list)


def meta(financial_year: int | None = None, notes: Iterable[str] = ()) -> Meta:
    row = fetch_one("SELECT cut_off_date FROM reporting_settings WHERE id = 1")
    return Meta(cut_off_date=row["cut_off_date"],
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
