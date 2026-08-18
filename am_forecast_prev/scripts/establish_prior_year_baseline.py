#!/usr/bin/env python3
"""Establish a month's Original Forecast from prior-year actual income.

Used for months that have no policy-level forecast. July 2026 is the case in
hand: the Renewals Pending file was extracted after most July renewals had
transacted, so there is no usable pending forecast for that month. The workbook
solved this with its Prior Year Actual row, and this reproduces it.

Not a migration, deliberately. A migration runs before any data is imported, so
it would find nothing to work with on a fresh install. This runs after the
imports, alongside the matcher.

Distinct from backfilling a month from its *own* actuals, which would make the
result its own target and is never done: prior-year actual is a genuine
observation, fixed before the period began.

    python scripts/establish_prior_year_baseline.py <dsn> --month=2026-07-01
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ACTOR = "script:prior_year_baseline"


def establish(conn, month: dt.date, replace_origins=("legacy_dashboard",)) -> dict:
    prior = dt.date(month.year - 1, month.month, 1)
    with conn.cursor() as cur:
        cur.execute("""SELECT count(*), COALESCE(SUM(actual_income), 0)
                       FROM sales_transaction
                       WHERE NOT is_excluded AND period_month = %s""", (prior,))
        rows, total = cur.fetchone()
        if not rows:
            raise SystemExit(f"no prior-year actuals for {prior}; nothing to establish")

        cur.execute("""DELETE FROM original_forecast
                       WHERE forecast_month = %s AND origin = ANY(%s)""",
                    (month, list(replace_origins)))
        removed = cur.rowcount

        cur.execute("""
            INSERT INTO original_forecast
              (grain, policy_id, forecast_month, financial_year, financial_quarter,
               origin, established_by, source_manager, expected_income,
               forecast_contribution, note)
            SELECT 'manager_month', NULL, %(month)s,
                   au_financial_year(%(month)s), au_quarter(%(month)s),
                   'prior_year_actual', %(actor)s, t.source_manager,
                   SUM(t.actual_income), GREATEST(SUM(t.actual_income), 0),
                   %(note)s
            FROM sales_transaction t
            WHERE NOT t.is_excluded AND t.period_month = %(prior)s
            GROUP BY t.source_manager
            HAVING SUM(t.actual_income) <> 0
            ON CONFLICT DO NOTHING
        """, {"month": month, "prior": prior, "actor": ACTOR,
              "note": f"Prior Year Actual: net actual income for {prior:%B %Y}, used "
                      f"as the {month:%B %Y} baseline. Manager-month grain; there is "
                      "no policy detail behind a prior-year total."})
        created = cur.rowcount

        cur.execute("""
            UPDATE forecast_baseline
            SET baseline_source = %s, manager_exceptions = '[]'::jsonb, note = %s
            WHERE forecast_month = %s
        """, (f"Prior Year Actual ({prior:%B %Y})",
              f"{month:%B %Y} is measured against {prior:%B %Y} actual income at "
              "manager-month level. There is no policy-level original forecast for "
              "this month.", month))

        cur.execute("""UPDATE legacy_forecast_reference
                       SET promoted_to_original = false
                       WHERE forecast_month = %s""", (month,))

        cur.execute("""SELECT COALESCE(SUM(forecast_contribution), 0)
                       FROM original_forecast WHERE forecast_month = %s""", (month,))
        established = cur.fetchone()[0]
    conn.commit()
    return {"month": month, "prior_month": prior, "removed": removed,
            "managers": created, "baseline_total": established,
            "prior_year_transactions": rows}


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dsn = sys.argv[1]
    month = dt.date.fromisoformat(
        next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--month=")),
             "2026-07-01"))
    with psycopg2.connect(dsn) as conn:
        r = establish(conn, month)
    print(f"{r['month']:%B %Y} baseline established from {r['prior_month']:%B %Y} "
          f"actuals: {r['managers']} managers, ${r['baseline_total']:,.2f} "
          f"(replaced {r['removed']} legacy rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
