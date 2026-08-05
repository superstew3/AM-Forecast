"""Staging area and sighting ledger.

Stage 2 is two-phase. Nothing reaches a fact table until a user accepts the
batch, so a preview is a real preview: it is computed from staged rows, not from
a dry run that has to be reasoned about separately.

`transaction_sighting` exists because rollback of a cumulative sales report is
otherwise ambiguous. A transaction seen in batches 1, 2 and 3 must survive
rollback of batch 3, and must have its `last_seen` restored to batch 2 rather
than left pointing at a batch that no longer exists. Recording every sighting
makes that deterministic instead of guesswork.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index,
    Integer, String, Text, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Money, actor, created_at, pk_big
from .reference import _in

STAGING_STATUSES = ("valid", "duplicate", "excluded", "rejected", "restated")


class ImportStaging(Base):
    """One parsed source row awaiting accept or reject.

    Cleared when the batch is accepted or rejected. Retained while pending so a
    user can leave a preview open, come back, and see the same numbers.
    """

    __tablename__ = "import_staging"

    id: Mapped[int] = pk_big()
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False, index=True)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)

    # Populated for sales rows.
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)
    existing_transaction_id: Mapped[int | None] = mapped_column(BigInteger)
    # Populated for renewals rows.
    policy_id: Mapped[int | None] = mapped_column(BigInteger)

    period_month: Mapped[dt.date | None] = mapped_column(Date)
    source_manager: Mapped[str | None] = mapped_column(String(120))
    category: Mapped[str | None] = mapped_column(String(20))

    positive_income = mapped_column(Money)
    return_income = mapped_column(Money)
    net_income = mapped_column(Money)
    expected_income = mapped_column(Money)
    forecast_contribution = mapped_column(Money)

    is_excluded: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))
    exclusion_rule_id: Mapped[int | None] = mapped_column(ForeignKey("exclusion_rule.id"))
    exclusion_field: Mapped[str | None] = mapped_column(String(60))
    exclusion_value: Mapped[str | None] = mapped_column(String(120))

    exception_flags = mapped_column(
        ARRAY(String(40)), nullable=False, server_default=text("'{}'::varchar[]"))
    reject_reason: Mapped[str | None] = mapped_column(Text)
    changed_fields = mapped_column(JSONB)

    # Fully typed, ready-to-insert values. Written once at stage time so accept
    # is a straight promotion and cannot re-derive a different answer.
    prepared = mapped_column(JSONB, nullable=False)
    source_row = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(_in("status", STAGING_STATUSES), name="staging_status"),
        UniqueConstraint("batch_id", "source_row_number", name="uq_staging_batch_row"),
        Index("ix_staging_pending", "batch_id", "status"),
    )


class TransactionSighting(Base):
    """Every time a transaction appeared in an accepted batch."""

    __tablename__ = "transaction_sighting"

    id: Mapped[int] = pk_big()
    transaction_id: Mapped[int] = mapped_column(
        ForeignKey("sales_transaction.id", ondelete="CASCADE"), nullable=False)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("upload_batch.id", ondelete="CASCADE"), nullable=False)
    source_row_number: Mapped[int | None] = mapped_column(Integer)
    seen_at = created_at()

    __table_args__ = (
        UniqueConstraint("transaction_id", "batch_id", name="uq_sighting_txn_batch"),
        Index("ix_sighting_batch", "batch_id"),
    )


class ColumnMappingProfile(Base):
    """A saved source-column to target-field mapping.

    Insurer exports drift. When a column is renamed, an administrator maps it
    once and the profile is reused, rather than the mapping being rediscovered
    or hardcoded.
    """

    __tablename__ = "column_mapping_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    profile_name: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    file_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mapping = mapped_column(JSONB, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = actor()
    created_at = created_at()

    __table_args__ = (
        Index("uq_default_profile_per_type", "file_type", unique=True,
              postgresql_where="is_default"),
    )


class BatchRollback(Base):
    """Audit of a rolled-back batch, including what it removed."""

    __tablename__ = "batch_rollback"

    id: Mapped[int] = pk_big()
    batch_id: Mapped[int] = mapped_column(ForeignKey("upload_batch.id"), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    performed_by: Mapped[str] = actor()
    performed_at = created_at()
    transactions_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sightings_removed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshots_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    original_forecast_rows_deleted: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0)
    net_income_reversed = mapped_column(Money)
    forecast_reversed = mapped_column(Money)
    detail = mapped_column(JSONB)
