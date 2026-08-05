"""Declarative base, shared types and controlled vocabularies.

Money is numeric(14,2) everywhere. No floats in the financial path.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, MetaData, Numeric, String, func
from sqlalchemy.orm import DeclarativeBase, mapped_column

# Explicit naming convention so Alembic autogenerate produces stable names.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --- shared column factories -------------------------------------------------

Money = Numeric(14, 2)
Rate = Numeric(6, 4)


def money(**kw):
    return mapped_column(Money, **kw)


def pk_big():
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def created_at():
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def actor(nullable: bool = False):
    """Who performed an action. Every mutating table carries one."""
    return mapped_column(String(120), nullable=nullable)


ZERO = Decimal("0.00")


# --- controlled vocabularies -------------------------------------------------
# Held in Python and mirrored as CHECK constraints. Anything not in these lists
# must fail loudly rather than being silently accepted.

FILE_TYPES = ("sales", "renewals", "legacy_forecast")

BATCH_STATUSES = ("pending", "accepted", "rejected", "rolled_back")

SOURCE_TYPES = ("sales", "renewals", "both")

MATCH_TYPES = ("exact", "contains")

FINANCIAL_DIRECTIONS = ("positive", "negative", "nil")

# Section 8 of the spec. Direction is derived independently of category.
BUSINESS_CLASSIFICATIONS = (
    "Renewal",
    "Transfer Renewal",
    "New Business",
    "Endorsement",
    "Lapse / End-Term Lost Renewal",
    "Mid-Term Cancellation",
    "New Business Cancellation",
    "Adjustment",
    "Endorsement Cancellation",
    "Policy Reinstatement",
    "Unmapped",
)

DERIVED_CLASSIFICATIONS = (
    "Positive Renewal",
    "Renewal Return or Correction",
    "Positive Transfer Renewal",
    "Transfer Renewal Return or Correction",
    "Positive New Business",
    "Negative New Business Correction",
    "New Business Cancellation",
    "Positive Endorsement",
    "Negative Endorsement",
    "Endorsement Cancellation",
    "Lapse / Lost Renewal",
    "Mid-Term Cancellation",
    "Positive Adjustment",
    "Negative Adjustment",
    "Policy Reinstatement",
    "Unmapped",
)

# A forecast policy may carry several of these simultaneously.
FORECAST_EXCEPTIONS = (
    "negative_expected",
    "zero_expected",
    "overdue_pending",
    "residual_pending",
)

# 'legacy_dashboard' added per the July 2026 decision. 'derived_from_actuals'
# is retained as a schema capability but is NOT used: deriving a forecast from
# the actual result would collapse the performance comparison it exists to make.
ORIGINAL_FORECAST_ORIGINS = (
    "snapshot",
    "legacy_dashboard",
    "rebaseline",
    "derived_from_actuals",
)

ORIGINAL_FORECAST_GRAINS = ("policy", "manager_month")

# Drives N/A rather than zero in every achievement calculation.
BASELINE_STATUSES = ("complete", "incomplete", "unavailable")

MANAGER_STATUSES = ("active", "legacy_unmapped", "inactive")

MOVEMENT_TYPES = (
    "removed_from_latest",
    "added_after_original",
    "amount_changed",
    "manager_changed",
    "detail_changed",
    "unchanged",
)

MATCH_STATUSES = (
    "matched_renewal",
    "matched_transfer_renewal",
    "matched_lapse",
    "pending",
    "removed_from_latest",
    "added_after_original",
    "multiple_candidate_matches",
    "unmatched_actual_renewal",
    "unmatched_forecast_policy",
    "manual_match",
    "match_rejected",
)

GROWTH_SCOPES = ("global", "manager", "manager_quarter")

COVERAGE_STATUSES = ("complete", "partial")


def australian_fy(d: dt.date) -> int:
    """Return the starting calendar year of the Australian financial year."""
    return d.year if d.month >= 7 else d.year - 1


def australian_quarter(d: dt.date) -> int:
    """Q1 Jul-Sep, Q2 Oct-Dec, Q3 Jan-Mar, Q4 Apr-Jun."""
    return ((d.month - 7) % 12) // 3 + 1
