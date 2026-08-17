"""Upload batches, sales transactions and the ingest exception queue."""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    text,
    BigInteger, Boolean, CheckConstraint, Computed, Date, DateTime, ForeignKey,
    Index, Integer, SmallInteger, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    BATCH_STATUSES, BUSINESS_CLASSIFICATIONS, DERIVED_CLASSIFICATIONS,
    FILE_TYPES, FINANCIAL_DIRECTIONS, Base, Money, actor, created_at, pk_big,
)
from .reference import _in


class UploadBatch(Base):
    """One accepted or rejected upload. Nothing enters the fact tables without
    a batch, and every batch is reversible."""

    __tablename__ = "upload_batch"

    id: Mapped[int] = pk_big()
    file_name: Mapped[str] = mapped_column(String(260), nullable=False)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    uploaded_by: Mapped[str] = actor()
    uploaded_at = created_at()
    accepted_by: Mapped[str | None] = actor(nullable=True)
    accepted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")

    source_row_count: Mapped[int | None] = mapped_column(Integer)
    accepted_row_count: Mapped[int | None] = mapped_column(Integer)
    duplicate_row_count: Mapped[int | None] = mapped_column(Integer)
    excluded_row_count: Mapped[int | None] = mapped_column(Integer)
    rejected_row_count: Mapped[int | None] = mapped_column(Integer)

    coverage_start: Mapped[dt.date | None] = mapped_column(Date)
    coverage_end: Mapped[dt.date | None] = mapped_column(Date)

    positive_income = mapped_column(Money)
    return_income = mapped_column(Money)
    net_income = mapped_column(Money)
    expected_forecast_income = mapped_column(Money)
    exception_count: Mapped[int | None] = mapped_column(Integer)

    rolled_back_by: Mapped[str | None] = actor(nullable=True)
    rolled_back_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rollback_reason: Mapped[str | None] = mapped_column(Text)

    validation_messages = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    column_mapping = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (
        CheckConstraint(_in("file_type", FILE_TYPES), name="batch_file_type"),
        CheckConstraint(_in("status", BATCH_STATUSES), name="batch_status"),
    )


class SalesTransaction(Base):
    """One source transaction line, deduplicated by stable fingerprint.

    Validated against the supplied file: 14,886 rows produce 14,886 distinct
    fingerprints. Re-uploading a cumulative report updates last_seen and
    increments seen_count; it inserts nothing and changes no total.
    """

    __tablename__ = "sales_transaction"

    id: Mapped[int] = pk_big()
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    first_seen_batch_id: Mapped[int] = mapped_column(
        ForeignKey("upload_batch.id"), nullable=False)
    first_seen_at = created_at()
    last_seen_batch_id: Mapped[int] = mapped_column(
        ForeignKey("upload_batch.id"), nullable=False)
    last_seen_at = created_at()
    seen_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))

    transaction_date: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=False), nullable=False)
    period_month: Mapped[dt.date] = mapped_column(Date, nullable=False)
    financial_year: Mapped[int] = mapped_column(Integer, nullable=False)
    financial_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    source_manager: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    group1_id: Mapped[int | None] = mapped_column(Integer)
    group2_description: Mapped[str | None] = mapped_column(String(120))

    client_id: Mapped[int | None] = mapped_column(BigInteger)
    client_code: Mapped[str | None] = mapped_column(String(60))
    client_code_norm: Mapped[str | None] = mapped_column(String(60))
    policy_number: Mapped[str | None] = mapped_column(String(120))
    policy_number_norm: Mapped[str | None] = mapped_column(String(120))
    invoice_number: Mapped[int | None] = mapped_column(BigInteger, index=True)
    username: Mapped[str | None] = mapped_column(String(120))

    category: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    business_classification: Mapped[str] = mapped_column(String(60), nullable=False)
    derived_classification: Mapped[str] = mapped_column(String(60), nullable=False)
    policy_class: Mapped[str | None] = mapped_column(String(60), index=True)
    uw_code: Mapped[str | None] = mapped_column(String(60), index=True)
    reason: Mapped[str | None] = mapped_column(Text)

    premium = mapped_column(Money)
    nett = mapped_column(Money)
    commission = mapped_column(Money, nullable=False)
    fees = mapped_column(Money, nullable=False)
    sub_comm = mapped_column(Money)

    # Actual Income = Commission + Fees. Fees is already the combined,
    # GST-inclusive amount. SpecialFees and Fee are components and are never
    # added; they live in source_row only.
    actual_income = mapped_column(
        Money, Computed("commission + fees", persisted=True), nullable=False)
    positive_income = mapped_column(
        Money, Computed("GREATEST(commission + fees, 0)", persisted=True), nullable=False)
    signed_return_income = mapped_column(
        Money, Computed("LEAST(commission + fees, 0)", persisted=True), nullable=False)
    absolute_return_income = mapped_column(
        Money, Computed("ABS(LEAST(commission + fees, 0))", persisted=True), nullable=False)
    financial_direction: Mapped[str] = mapped_column(String(10), nullable=False)

    primary_assoc_code: Mapped[str | None] = mapped_column(String(60))
    primary_assoc_amount = mapped_column(Money)
    secondary_assoc_code: Mapped[str | None] = mapped_column(String(60))
    secondary_assoc_amount = mapped_column(Money)

    is_excluded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    exclusion_rule_id: Mapped[int | None] = mapped_column(ForeignKey("exclusion_rule.id"))
    exclusion_field: Mapped[str | None] = mapped_column(String(60))
    exclusion_value: Mapped[str | None] = mapped_column(String(120))

    source_row = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(_in("financial_direction", FINANCIAL_DIRECTIONS),
                        name="txn_financial_direction"),
        CheckConstraint(_in("business_classification", BUSINESS_CLASSIFICATIONS),
                        name="txn_business_classification"),
        CheckConstraint(_in("derived_classification", DERIVED_CLASSIFICATIONS),
                        name="txn_derived_classification"),
        CheckConstraint("financial_quarter BETWEEN 1 AND 4", name="txn_quarter_range"),
        CheckConstraint(
            "(is_excluded = false AND exclusion_rule_id IS NULL) OR "
            "(is_excluded = true AND exclusion_rule_id IS NOT NULL)",
            name="txn_exclusion_consistency"),
        Index("ix_txn_reporting", "period_month", "source_manager",
              postgresql_where="NOT is_excluded"),
        Index("ix_txn_match_keys", "client_code_norm", "policy_number_norm"),
        Index("ix_txn_fy_quarter", "financial_year", "financial_quarter",
              postgresql_where="NOT is_excluded"),
    )


class RestatedTransaction(Base):
    """A fingerprint reappeared with a changed non-key field.

    Never silently overwritten. Held for review so a restated commission or a
    corrected policy class is a decision, not an accident.
    """

    __tablename__ = "restated_transaction"

    id: Mapped[int] = pk_big()
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("sales_transaction.id"), nullable=False)
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), nullable=False)
    changed_fields = mapped_column(JSONB, nullable=False)
    detected_at = created_at()
    resolved_by: Mapped[str | None] = actor(nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    resolution: Mapped[str | None] = mapped_column(String(30))
    note: Mapped[str | None] = mapped_column(Text)


class IngestException(Base):
    """Data-quality queue backing reporting view G."""

    __tablename__ = "ingest_exception"

    id: Mapped[int] = pk_big()
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), nullable=False)
    exception_type: Mapped[str] = mapped_column(String(60), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="warning")
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    field_name: Mapped[str | None] = mapped_column(String(60))
    field_value: Mapped[str | None] = mapped_column(Text)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    payload = mapped_column(JSONB)
    detected_at = created_at()
    resolved_by: Mapped[str | None] = actor(nullable=True)
    resolved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("severity IN ('info', 'warning', 'error')",
                        name="exception_severity"),
    )
