#!/usr/bin/env python3
"""Set a month's renewal forecast from a supplied figure per manager.

Some months have no usable pending forecast. July 2026 is the case in hand: the
Renewals Pending file was extracted after most July renewals had transacted, so
there is nothing in it to forecast from. The figures are supplied directly
instead, in a CSV that can be edited without touching code.

    python scripts/set_month_forecast.py <dsn> data/month_forecast_2026-07.csv \
        --month=2026-07-01

The CSV needs two columns: canonical_manager, forecast_amount.
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ACTOR = "script:set_month_forecast"
# Origins this replaces. Anything previously establishing the month gives way.
REPLACES = ("legacy_dashboard", "prior_year_actual", "manual_entry")


def australian_fy(d: dt.date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def australian_quarter(d: dt.date) -> int:
    return ((d.month - 7) % 12) // 3 + 1


def load(path: str) -> list[tuple[str, Decimal]]:
    rows = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("canonical_manager") or "").strip()
            if not name:
                continue
            rows.append((name, Decimal(str(r["forecast_amount"]).replace(",", ""))))
    if not rows:
        raise SystemExit(f"no rows in {path}")
    return rows


def apply(conn, month: dt.date, rows: list[tuple[str, Decimal]]) -> dict:
    with conn.cursor() as cur:
        cur.execute("SELECT canonical_manager FROM reporting_manager")
        known = {r[0] for r in cur.fetchall()}
        unknown = [n for n, _ in rows if n not in known]
        if unknown:
            raise SystemExit(
                "unknown reporting manager(s): " + ", ".join(unknown)
                + ". Create them first, or correct the spelling in the CSV.")

        cur.execute("""DELETE FROM original_forecast
                       WHERE forecast_month = %s AND grain = 'manager_month'
                         AND origin = ANY(%s)""", (month, list(REPLACES)))
        removed = cur.rowcount

        for name, amount in rows:
            cur.execute("""
                INSERT INTO original_forecast
                  (grain, policy_id, forecast_month, financial_year,
                   financial_quarter, origin, established_by, source_manager,
                   expected_income, forecast_contribution, note)
                VALUES ('manager_month', NULL, %s, %s, %s, 'manual_entry', %s, %s,
                        %s, GREATEST(%s, 0), %s)
                ON CONFLICT DO NOTHING""",
                        (month, australian_fy(month), australian_quarter(month),
                         ACTOR, name, amount, amount,
                         f"Renewal forecast for {month:%B %Y}, supplied per manager. "
                         "Manager-month grain: there is no policy detail behind it."))

        cur.execute("""
            UPDATE forecast_baseline
            SET baseline_source = %s, manager_exceptions = '[]'::jsonb, note = %s
            WHERE forecast_month = %s""",
                    (f"Supplied figures ({month:%B %Y})",
                     f"{month:%B %Y} uses supplied per-manager forecast figures at "
                     "manager-month level. There is no policy-level detail for this "
                     "month.", month))

        cur.execute("""UPDATE legacy_forecast_reference SET promoted_to_original = false
                       WHERE forecast_month = %s""", (month,))

        cur.execute("""SELECT COALESCE(SUM(forecast_contribution), 0), COUNT(*)
                       FROM original_forecast WHERE forecast_month = %s""", (month,))
        total, count = cur.fetchone()
    conn.commit()
    return {"month": month, "managers": count, "total": total, "replaced": removed}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    dsn, path = sys.argv[1], sys.argv[2]
    month = dt.date.fromisoformat(
        next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--month=")),
             "2026-07-01"))
    with psycopg2.connect(dsn) as conn:
        r = apply(conn, month, load(path))
    print(f"{r['month']:%B %Y} forecast set for {r['managers']} managers: "
          f"${r['total']:,.2f} (replaced {r['replaced']} previous rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
