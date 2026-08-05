"""Forecast snapshots, the frozen original, movement and legacy reference series."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    text,
    BigInteger, Boolean, CheckConstraint, Computed, Date, DateTime, ForeignKey,
    Index, Integer, SmallInteger, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    FORECAST_EXCEPTIONS, MOVEMENT_TYPES, ORIGINAL_FORECAST_GRAINS,
    ORIGINAL_FORECAST_ORIGINS, Base, Money, actor, created_at, pk_big,
)
from .reference import _in


class ForecastSnapshot(Base):
    """An immutable Renewals Pending upload. Never merged into a prior snapshot."""

    __tablename__ = "forecast_snapshot"

    id: Mapped[int] = pk_big()
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), nullable=False)
    as_of_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    coverage_start: Mapped[dt.date] = mapped_column(Date, nullable=False)
    coverage_end: Mapped[dt.date] = mapped_column(Date, nullable=False)

    source_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    included_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    excluded_row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    negative_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    zero_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    overdue_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))

    raw_expected_income = mapped_column(Money, nullable=False)
    forecast_contribution = mapped_column(Money, nullable=False)

    is_superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    validation_messages = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at = created_at()


class ForecastPolicy(Base):
    """One pending policy within one snapshot.

    PolicyID is the identifier within a snapshot and is expected to recur across
    snapshots. Never deduplicated on policy number, or on client plus policy
    number plus expiry — the source legitimately repeats those with distinct
    PolicyIDs (3 such groups in the supplied file).
    """

    __tablename__ = "forecast_policy"

    id: Mapped[int] = pk_big()
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshot.id"), nullable=False)
    policy_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    client_id: Mapped[int | None] = mapped_column(BigInteger)
    client_code: Mapped[str | None] = mapped_column(String(60))
    client_code_norm: Mapped[str | None] = mapped_column(String(60))
    policy_number: Mapped[str | None] = mapped_column(String(120))
    policy_number_norm: Mapped[str | None] = mapped_column(String(120))
    class_abbrev: Mapped[str | None] = mapped_column(String(60), index=True)
    class_code: Mapped[str | None] = mapped_column(String(60))
    class_description: Mapped[str | None] = mapped_column(String(160))
    underwriter_abbrev: Mapped[str | None] = mapped_column(String(60), index=True)

    inception_date: Mapped[dt.date | None] = mapped_column(Date)
    expiry_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    # Retained for reference. Explicitly NOT the forecast date.
    next_expiry_date: Mapped[dt.date | None] = mapped_column(Date)
    renewal_months: Mapped[int | None] = mapped_column(Integer)

    forecast_month: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    financial_year: Mapped[int] = mapped_column(Integer, nullable=False)
    financial_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    source_manager: Mapped[str] = mapped_column(String(120), nullable=False, index=True)

    # Comm/Fee are GST exclusive with separate tax columns. Fee is already
    # Admin + Special and FeeTax already AdminTax + SpecialTax — verified to
    # 0.0000 across all 5,774 included rows. Components are never added.
    comm = mapped_column(Money, nullable=False)
    comm_tax = mapped_column(Money, nullable=False)
    fee = mapped_column(Money, nullable=False)
    fee_tax = mapped_column(Money, nullable=False)
    premium = mapped_column(Money)
    total_premium = mapped_column(Money)

    raw_expected_income = mapped_column(
        Money, Computed("comm + comm_tax + fee + fee_tax", persisted=True), nullable=False)
    forecast_contribution = mapped_column(
        Money, Computed("GREATEST(comm + comm_tax + fee + fee_tax, 0)", persisted=True),
        nullable=False)

    # A row can hold several at once (e.g. zero expected AND residual pending),
    # so this is an array, not a single value.
    exception_flags = mapped_column(ARRAY(String(30)), nullable=False,
                                    server_default=text("'{}'::varchar[]"))

    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    exclusion_rule_id: Mapped[int | None] = mapped_column(ForeignKey("exclusion_rule.id"))
    exclusion_field: Mapped[str | None] = mapped_column(String(60))
    exclusion_value: Mapped[str | None] = mapped_column(String(120))

    source_row = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("snapshot_id", "policy_id", name="uq_forecast_policy_snapshot_policy"),
        CheckConstraint(
            "exception_flags <@ ARRAY["
            + ", ".join(f"'{v}'" for v in FORECAST_EXCEPTIONS)
            + "]::varchar[]",
            name="forecast_exception_flags"),
        CheckConstraint("financial_quarter BETWEEN 1 AND 4", name="fcst_quarter_range"),
        Index("ix_fcst_reporting", "snapshot_id", "forecast_month", "source_manager",
              postgresql_where="NOT is_excluded"),
        Index("ix_fcst_match_keys", "client_code_norm", "policy_number_norm"),
    )


class OriginalForecast(Base):
    """The frozen baseline. Written once per policy or manager-month, never
    updated by a normal upload.

    Two grains are supported:
      - 'policy'        : from a Renewals Pending snapshot (the normal case)
      - 'manager_month' : from the legacy dashboard, which only ever held
                          manager-month totals. policy_id is NULL for these.

    Origin 'derived_from_actuals' exists in the vocabulary but is deliberately
    unused: deriving the baseline from the result destroys the comparison.
    """

    __tablename__ = "original_forecast"

    id: Mapped[int] = pk_big()
    grain: Mapped[str] = mapped_column(String(20), nullable=False, default="policy", server_default=text("'policy'"))
    policy_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    forecast_month: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    financial_year: Mapped[int] = mapped_column(Integer, nullable=False)
    financial_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    origin: Mapped[str] = mapped_column(String(30), nullable=False, default="snapshot", server_default=text("'snapshot'"))
    established_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("forecast_snapshot.id"))
    established_batch_id: Mapped[int | None] = mapped_column(ForeignKey("upload_batch.id"))
    established_by: Mapped[str] = actor()
    established_at = created_at()

    source_manager: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    client_code: Mapped[str | None] = mapped_column(String(60))
    policy_number: Mapped[str | None] = mapped_column(String(120))
    class_abbrev: Mapped[str | None] = mapped_column(String(60))

    expected_income = mapped_column(Money, nullable=False)
    forecast_contribution = mapped_column(Money, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint(_in("grain", ORIGINAL_FORECAST_GRAINS), name="orig_grain"),
        CheckConstraint(_in("origin", ORIGINAL_FORECAST_ORIGINS), name="orig_origin"),
        CheckConstraint(
            "(grain = 'policy' AND policy_id IS NOT NULL) OR "
            "(grain = 'manager_month' AND policy_id IS NULL)",
            name="orig_grain_policy_consistency"),
        CheckConstraint("forecast_contribution >= 0", name="orig_contribution_non_negative"),
        # One original per policy-month, and one per manager-month.
        Index("uq_orig_policy", "policy_id", "forecast_month", unique=True,
              postgresql_where="grain = 'policy'"),
        Index("uq_orig_manager_month", "source_manager", "forecast_month", unique=True,
              postgresql_where="grain = 'manager_month'"),
    )


class ForecastMonthCoverage(Base):
    """Which snapshot established the original for a month, and which is latest."""

    __tablename__ = "forecast_month_coverage"

    forecast_month: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    original_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("forecast_snapshot.id"))
    latest_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("forecast_snapshot.id"))
    original_grain: Mapped[str] = mapped_column(String(20), nullable=False, default="policy", server_default=text("'policy'"))
    established_at = created_at()

    __table_args__ = (
        CheckConstraint(_in("original_grain", ORIGINAL_FORECAST_GRAINS),
                        name="coverage_original_grain"),
    )


class ForecastMovement(Base):
    """Original -> Latest movement, per policy per month.

    A removal records the amount removed and never writes negative income. The
    monthly Latest total is floored at zero by construction because every
    contribution is GREATEST(raw, 0).
    """

    __tablename__ = "forecast_movement"

    id: Mapped[int] = pk_big()
    from_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("forecast_snapshot.id"))
    to_snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshot.id"), nullable=False)
    policy_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    forecast_month: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    movement_type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)

    original_income = mapped_column(Money, nullable=False, server_default=text("0"))
    previous_income = mapped_column(Money, nullable=False, server_default=text("0"))
    latest_income = mapped_column(Money, nullable=False, server_default=text("0"))
    movement_amount = mapped_column(Money, nullable=False, server_default=text("0"))

    from_manager: Mapped[str | None] = mapped_column(String(120))
    to_manager: Mapped[str | None] = mapped_column(String(120))
    detail_changes = mapped_column(JSONB)
    detected_at = created_at()

    __table_args__ = (
        CheckConstraint(_in("movement_type", MOVEMENT_TYPES), name="movement_type"),
        CheckConstraint("latest_income >= 0", name="movement_latest_non_negative"),
    )


class LegacyForecastReference(Base):
    """Manager-month forecast values carried from the old workbook.

    Only the months explicitly promoted into original_forecast are used as a
    baseline. The rest are held as a comparison series so the divergence
    between the legacy dashboard and the pending-renewals baseline stays
    visible instead of being quietly discarded.
    """

    __tablename__ = "legacy_forecast_reference"

    id: Mapped[int] = pk_big()
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("upload_batch.id"))
    forecast_month: Mapped[dt.date] = mapped_column(Date, nullable=False, index=True)
    financial_year: Mapped[int] = mapped_column(Integer, nullable=False)
    financial_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    source_manager: Mapped[str] = mapped_column(String(120), nullable=False)
    forecast_amount = mapped_column(Money, nullable=False)
    promoted_to_original: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    is_verified_exclusion_clean: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"))
    note: Mapped[str | None] = mapped_column(Text)
    loaded_at = created_at()

    __table_args__ = (
        UniqueConstraint("forecast_month", "source_manager",
                         name="uq_legacy_forecast_month_manager"),
    )


class RebaselineAudit(Base):
    """Administrator-only deliberate rebaseline of a frozen original."""

    __tablename__ = "rebaseline_audit"

    id: Mapped[int] = pk_big()
    scope_description: Mapped[str] = mapped_column(Text, nullable=False)
    forecast_month_from: Mapped[dt.date] = mapped_column(Date, nullable=False)
    forecast_month_to: Mapped[dt.date] = mapped_column(Date, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    performed_by: Mapped[str] = actor()
    performed_at = created_at()
    before_total = mapped_column(Money, nullable=False)
    after_total = mapped_column(Money, nullable=False)
    before_detail = mapped_column(JSONB)
    after_detail = mapped_column(JSONB)
