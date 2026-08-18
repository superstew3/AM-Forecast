"""Budget configuration, matching and general audit."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    text,
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
    Integer, Numeric, SmallInteger, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import GROWTH_SCOPES, MATCH_STATUSES, Base, Money, Rate, actor, created_at, pk_big
from .reference import _in


class GrowthRate(Base):
    """Adjustable new business growth target.

    Resolution order, most specific wins:
      manager_quarter -> manager -> global
    A dollar override at any level supersedes the percentage at that level and
    the active basis is always reported alongside the number.
    """

    __tablename__ = "growth_rate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(String(20), nullable=False)
    canonical_manager: Mapped[str | None] = mapped_column(
        ForeignKey("reporting_manager.canonical_manager", onupdate="CASCADE"))
    financial_year: Mapped[int | None] = mapped_column(Integer)
    financial_quarter: Mapped[int | None] = mapped_column(SmallInteger)

    growth_pct = mapped_column(Rate)
    dollar_override = mapped_column(Money)

    note: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_by: Mapped[str] = actor()
    created_at = created_at()
    superseded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(_in("scope", GROWTH_SCOPES), name="growth_scope"),
        CheckConstraint("growth_pct IS NOT NULL OR dollar_override IS NOT NULL",
                        name="growth_value_present"),
        CheckConstraint(
            "(scope = 'global' AND canonical_manager IS NULL AND financial_quarter IS NULL) OR "
            "(scope = 'manager' AND canonical_manager IS NOT NULL AND financial_quarter IS NULL) OR "
            "(scope = 'manager_quarter' AND canonical_manager IS NOT NULL "
            " AND financial_year IS NOT NULL AND financial_quarter IS NOT NULL)",
            name="growth_scope_consistency"),
        CheckConstraint("financial_quarter IS NULL OR financial_quarter BETWEEN 1 AND 4",
                        name="growth_quarter_range"),
        Index("uq_growth_global", "scope", unique=True,
              postgresql_where="scope = 'global' AND active"),
        Index("uq_growth_manager", "canonical_manager", "financial_year", unique=True,
              postgresql_where="scope = 'manager' AND active"),
        Index("uq_growth_manager_quarter", "canonical_manager", "financial_year",
              "financial_quarter", unique=True,
              postgresql_where="scope = 'manager_quarter' AND active"),
    )


class MonthlyTargetOverride(Base):
    """Manual override of the monthly allocation of a quarterly growth target.

    Default allocation weights each month by its share of that quarter's
    Original Renewal Forecast, not an equal third — the renewal pattern is
    materially uneven (FY26-27 Q4 $1.10M against Q1 $616k; December $141k
    against November $381k).
    """

    __tablename__ = "monthly_target_override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_manager: Mapped[str] = mapped_column(
        ForeignKey("reporting_manager.canonical_manager", onupdate="CASCADE"), nullable=False)
    target_month: Mapped[dt.date] = mapped_column(Date, nullable=False)
    override_amount = mapped_column(Money, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = actor()
    created_at = created_at()
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (
        Index("uq_monthly_override", "canonical_manager", "target_month", unique=True,
              postgresql_where="active"),
    )


class BudgetAudit(Base):
    """Every budget-affecting change, with before and after."""

    __tablename__ = "budget_audit"

    id: Mapped[int] = pk_big()
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    scope_description: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_manager: Mapped[str | None] = mapped_column(String(120))
    financial_year: Mapped[int | None] = mapped_column(Integer)
    financial_quarter: Mapped[int | None] = mapped_column(SmallInteger)
    before_value = mapped_column(JSONB)
    after_value = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    performed_by: Mapped[str] = actor()
    performed_at = created_at()


class ForecastActualMatch(Base):
    """Forecast policy to actual transaction match.

    Tier 1 client+policy+date-in-tolerance, down to tier 4 which always goes to
    the review queue. Multiple candidates at the winning tier is never resolved
    automatically.
    """

    __tablename__ = "forecast_actual_match"

    id: Mapped[int] = pk_big()
    policy_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    forecast_month: Mapped[dt.date | None] = mapped_column(Date, index=True)
    transaction_id: Mapped[int | None] = mapped_column(ForeignKey("sales_transaction.id"))
    match_status: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    match_method: Mapped[str] = mapped_column(String(10), nullable=False, default="auto")
    match_tier: Mapped[int | None] = mapped_column(SmallInteger)
    confidence = mapped_column(Numeric(4, 3))
    matched_income = mapped_column(Money)
    candidate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    requires_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    reviewed_by: Mapped[str | None] = actor(nullable=True)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at = created_at()

    __table_args__ = (
        CheckConstraint(_in("match_status", MATCH_STATUSES), name="match_status"),
        CheckConstraint("match_method IN ('auto', 'manual')", name="match_method"),
        CheckConstraint("confidence IS NULL OR confidence BETWEEN 0 AND 1",
                        name="match_confidence_range"),
    )


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="viewer")
    password_hash: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    created_at = created_at()

    __table_args__ = (
        CheckConstraint("role IN ('viewer', 'manager', 'administrator')", name="user_role"),
    )
