"""Reference and configuration tables.

Every mapping the business depends on lives here and nowhere else. No manager
name, exclusion string or category code is hardcoded in a query or calculation.
"""
from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    text,
    Boolean, CheckConstraint, Date, ForeignKey, Integer, SmallInteger, String,
    Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import (
    BASELINE_STATUSES, BUSINESS_CLASSIFICATIONS, COVERAGE_STATUSES,
    MANAGER_STATUSES, MATCH_TYPES, SOURCE_TYPES, Base, Money, actor, created_at,
)


def _in(col: str, values) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{col} IN ({joined})"


class ReportingManager(Base):
    """Canonical reporting managers.

    `status` and `include_in_rankings` are separate on purpose. Anastasia K is
    'legacy_unmapped' and out of rankings, but her actual income still counts
    towards business totals — those are different questions.
    """

    __tablename__ = "reporting_manager"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    canonical_manager: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    include_in_rankings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_in_business_totals: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    display_order: Mapped[int | None] = mapped_column(Integer)
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = actor()
    updated_at = created_at()

    __table_args__ = (
        CheckConstraint(_in("status", MANAGER_STATUSES), name="manager_status"),
    )


class ManagerAlias(Base):
    """Source manager -> canonical reporting manager.

    One table, applied identically to actuals, forecasts and budgets. Canonical
    manager is resolved through a join at read time, never denormalised onto
    fact rows, so correcting an alias retrospectively fixes every report.
    """

    __tablename__ = "manager_alias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_manager: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    source_manager_norm: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    canonical_manager: Mapped[str] = mapped_column(
        ForeignKey("reporting_manager.canonical_manager", onupdate="CASCADE"), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = actor()
    updated_at = created_at()


class ExclusionRule(Base):
    """Configurable exclusion rules. Seeded with the Highview rules.

    Records matching a rule are imported and retained in full, flagged
    `is_excluded`, and omitted from reported totals by the reporting views.
    They are never dropped at the door.
    """

    __tablename__ = "exclusion_rule"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_group: Mapped[str] = mapped_column(String(60), nullable=False, default="highview")
    rule_name: Mapped[str] = mapped_column(String(120), nullable=False)
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)
    target_field: Mapped[str] = mapped_column(String(60), nullable=False)
    match_type: Mapped[str] = mapped_column(String(20), nullable=False)
    match_value: Mapped[str] = mapped_column(String(120), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = actor()
    updated_at = created_at()

    __table_args__ = (
        CheckConstraint(_in("source_type", SOURCE_TYPES), name="exclusion_source_type"),
        CheckConstraint(_in("match_type", MATCH_TYPES), name="exclusion_match_type"),
        UniqueConstraint("source_type", "target_field", "match_type", "match_value",
                         name="uq_exclusion_rule_definition"),
    )


class CategoryMap(Base):
    """Transaction category -> readable business classification.

    An unmapped category is never silently assigned. Ingest writes it to the
    exception queue and classifies it 'Unmapped'.
    """

    __tablename__ = "category_map"

    category: Mapped[str] = mapped_column(String(10), primary_key=True)
    business_classification: Mapped[str] = mapped_column(String(60), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))

    __table_args__ = (
        CheckConstraint(_in("business_classification", BUSINESS_CLASSIFICATIONS),
                        name="category_business_classification"),
    )


class ForecastBaseline(Base):
    """Per-month declaration of how trustworthy the Original Forecast is.

    This is the mechanism that makes July 2026 report N/A instead of zero.
    Every achievement calculation joins here first; a month that is not
    'complete' returns NULL, never a percentage.
    """

    __tablename__ = "forecast_baseline"

    forecast_month: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    financial_year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    financial_quarter: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    baseline_status: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_source: Mapped[str | None] = mapped_column(String(60))
    suppress_achievement: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default=text("false"))
    # Canonical managers for whom this month's baseline is not usable even
    # though the month as a whole is. Achievement returns N/A for these.
    manager_exceptions = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    note: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = actor()
    updated_at = created_at()

    __table_args__ = (
        CheckConstraint(_in("baseline_status", BASELINE_STATUSES), name="baseline_status"),
        CheckConstraint("financial_quarter BETWEEN 1 AND 4", name="baseline_quarter_range"),
    )


class PeriodCoverage(Base):
    """Which financial years and months the loaded data actually covers.

    Prevents a two-month fragment being read as a year. May-Jun 2025 is
    'partial' and is labelled as such everywhere it appears.
    """

    __tablename__ = "period_coverage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    financial_year: Mapped[int] = mapped_column(Integer, nullable=False)
    data_domain: Mapped[str] = mapped_column(String(20), nullable=False)  # actuals | forecast
    coverage_status: Mapped[str] = mapped_column(String(20), nullable=False)
    months_present: Mapped[int] = mapped_column(Integer, nullable=False)
    first_month: Mapped[dt.date] = mapped_column(Date, nullable=False)
    last_month: Mapped[dt.date] = mapped_column(Date, nullable=False)
    label: Mapped[str | None] = mapped_column(String(160))
    updated_at = created_at()

    __table_args__ = (
        CheckConstraint(_in("coverage_status", COVERAGE_STATUSES), name="coverage_status"),
        UniqueConstraint("financial_year", "data_domain", name="uq_period_coverage_fy_domain"),
    )


class ReportingSettings(Base):
    """Single-row settings table."""

    __tablename__ = "reporting_settings"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    cut_off_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    cut_off_set_by: Mapped[str] = actor()
    cut_off_set_at = created_at()
    match_date_tolerance_days: Mapped[int] = mapped_column(Integer, nullable=False, default=45)
    default_growth_pct = mapped_column(Money, nullable=False)
    gst_note: Mapped[str] = mapped_column(
        Text, nullable=False, default="All income figures are GST inclusive."
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="reporting_settings_singleton"),
        CheckConstraint("match_date_tolerance_days BETWEEN 0 AND 365",
                        name="match_tolerance_range"),
    )
