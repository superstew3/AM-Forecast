#!/usr/bin/env python3
"""Load Stage 1 reference data.

Idempotent: safe to run repeatedly. Uses ON CONFLICT DO UPDATE so correcting a
seed value and re-running updates in place rather than duplicating.

Usage:
    python -m app.seed.load_seed [dsn]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.seed.class_equivalence import class_equivalence_rows  # noqa: E402
from app.seed.seed_data import (  # noqa: E402
    CATEGORY_MAP, DEFAULT_GROWTH_RATE, EXCLUSION_RULES, MANAGER_ALIASES,
    PERIOD_COVERAGE, REPORTING_MANAGERS, REPORTING_SETTINGS,
    forecast_baselines, fy2025_26_baselines,
)

SEED_ACTOR = "system:seed"


def normalise(value: str | None) -> str:
    """Shared normalisation for manager and associate matching.

    Uppercase, punctuation to space, whitespace collapsed, trimmed. Applied
    identically to seeded rule values and to incoming source fields, so the
    comparison is like for like.
    """
    if value is None:
        return ""
    s = str(value).upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def au_fy(d):
    return d.year if d.month >= 7 else d.year - 1


def au_q(d):
    return ((d.month - 7) % 12) // 3 + 1


def load(conn) -> dict:
    counts = {}
    with conn.cursor() as cur:
        for m in REPORTING_MANAGERS:
            cur.execute("""
                INSERT INTO reporting_manager
                    (canonical_manager, status, include_in_rankings,
                     include_in_business_totals, display_order, note, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (canonical_manager) DO UPDATE SET
                    status = EXCLUDED.status,
                    include_in_rankings = EXCLUDED.include_in_rankings,
                    include_in_business_totals = EXCLUDED.include_in_business_totals,
                    display_order = EXCLUDED.display_order,
                    note = EXCLUDED.note
            """, (*m, SEED_ACTOR))
        counts["reporting_manager"] = len(REPORTING_MANAGERS)

        for source, canonical, note in MANAGER_ALIASES:
            cur.execute("""
                INSERT INTO manager_alias
                    (source_manager, source_manager_norm, canonical_manager,
                     active, note, updated_by)
                VALUES (%s,%s,%s,true,%s,%s)
                ON CONFLICT (source_manager) DO UPDATE SET
                    canonical_manager = EXCLUDED.canonical_manager,
                    source_manager_norm = EXCLUDED.source_manager_norm,
                    note = EXCLUDED.note
            """, (source, normalise(source), canonical, note, SEED_ACTOR))
        counts["manager_alias"] = len(MANAGER_ALIASES)

        for group, name, stype, field, mtype, value, note in EXCLUSION_RULES:
            cur.execute("""
                INSERT INTO exclusion_rule
                    (rule_group, rule_name, source_type, target_field,
                     match_type, match_value, active, note, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,true,%s,%s)
                ON CONFLICT (source_type, target_field, match_type, match_value)
                DO UPDATE SET rule_name = EXCLUDED.rule_name, note = EXCLUDED.note
            """, (group, name, stype, field, mtype, normalise(value), note, SEED_ACTOR))
        counts["exclusion_rule"] = len(EXCLUSION_RULES)

        for cat, cls, desc in CATEGORY_MAP:
            cur.execute("""
                INSERT INTO category_map (category, business_classification, description, active)
                VALUES (%s,%s,%s,true)
                ON CONFLICT (category) DO UPDATE SET
                    business_classification = EXCLUDED.business_classification,
                    description = EXCLUDED.description
            """, (cat, cls, desc))
        counts["category_map"] = len(CATEGORY_MAP)

        baselines = forecast_baselines() + fy2025_26_baselines()
        for b in baselines:
            m = b["forecast_month"]
            cur.execute("""
                INSERT INTO forecast_baseline
                    (forecast_month, financial_year, financial_quarter, baseline_status,
                     baseline_source, suppress_achievement, manager_exceptions, note, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s)
                ON CONFLICT (forecast_month) DO UPDATE SET
                    baseline_status = EXCLUDED.baseline_status,
                    baseline_source = EXCLUDED.baseline_source,
                    suppress_achievement = EXCLUDED.suppress_achievement,
                    manager_exceptions = EXCLUDED.manager_exceptions,
                    note = EXCLUDED.note
            """, (m, au_fy(m), au_q(m), b["baseline_status"], b["baseline_source"],
                  b["suppress_achievement"], json.dumps(b["manager_exceptions"]),
                  b["note"], SEED_ACTOR))
        counts["forecast_baseline"] = len(baselines)

        for fy, domain, status, months, first, last, label in PERIOD_COVERAGE:
            cur.execute("""
                INSERT INTO period_coverage
                    (financial_year, data_domain, coverage_status, months_present,
                     first_month, last_month, label)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (financial_year, data_domain) DO UPDATE SET
                    coverage_status = EXCLUDED.coverage_status,
                    months_present = EXCLUDED.months_present,
                    first_month = EXCLUDED.first_month,
                    last_month = EXCLUDED.last_month,
                    label = EXCLUDED.label
            """, (fy, domain, status, months, first, last, label))
        counts["period_coverage"] = len(PERIOD_COVERAGE)

        s = REPORTING_SETTINGS
        cur.execute("""
            INSERT INTO reporting_settings
                (id, cut_off_date, cut_off_set_by, match_date_tolerance_days,
                 default_growth_pct, gst_note)
            VALUES (1,%s,%s,%s,%s,%s)
            ON CONFLICT (id) DO UPDATE SET
                -- Deliberately NOT cut_off_date. It is an operational setting an
                -- administrator moves as each month closes, and re-running the
                -- seed silently reverting it would make completed months look
                -- open again — which reads as a data fault rather than a
                -- settings one, and is very hard to spot.
                match_date_tolerance_days = EXCLUDED.match_date_tolerance_days,
                default_growth_pct = EXCLUDED.default_growth_pct,
                gst_note = EXCLUDED.gst_note
        """, (s["cut_off_date"], SEED_ACTOR, s["match_date_tolerance_days"],
              s["default_growth_pct"], s["gst_note"]))
        counts["reporting_settings"] = 1

        rows = class_equivalence_rows()
        for source_type, source_value, canonical in rows:
            cur.execute("""
                INSERT INTO class_equivalence
                    (source_type, source_value, canonical_class, updated_by)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (source_type, source_value) DO UPDATE SET
                    canonical_class = EXCLUDED.canonical_class
            """, (source_type, source_value, canonical, SEED_ACTOR))
        counts["class_equivalence"] = len(rows)

        g = DEFAULT_GROWTH_RATE
        cur.execute("SELECT 1 FROM growth_rate WHERE scope='global' AND active")
        if not cur.fetchone():
            cur.execute("""
                INSERT INTO growth_rate (scope, growth_pct, note, active, created_by)
                VALUES ('global',%s,%s,true,%s)
            """, (g["growth_pct"], g["note"], SEED_ACTOR))
        counts["growth_rate"] = 1

    conn.commit()
    return counts


def main() -> int:
    dsn = sys.argv[1] if len(sys.argv) > 1 else "dbname=am_forecast"
    with psycopg2.connect(dsn) as conn:
        counts = load(conn)
    for k, v in counts.items():
        print(f"  {k:24s} {v:>4d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
