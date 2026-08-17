#!/usr/bin/env python3
"""Establish one month's Original Forecast from a specific Renewals Pending file.

For a month whose renewals had already transacted by the time the current
export was taken. The August extract holds two residual July policies, because
the rest had renewed and moved to the Sales Transaction report — so it cannot
say what July was expected to earn. An earlier extract, taken while those
renewals were still pending, can.

The month is pinned once established, so a later snapshot cannot overwrite a
figure that people were measured against.

    python scripts/set_month_forecast_from_file.py <dsn> <renewals.csv> \
        --month=2026-07-01 --reason="April extract; July had not yet renewed"
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.importers.engine import ExclusionEngine  # noqa: E402
from app.importers.normalise import dec, parse_date  # noqa: E402

ACTOR = "script:set_month_forecast_from_file"


def australian_fy(d: dt.date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def australian_quarter(d: dt.date) -> int:
    return ((d.month - 7) % 12) // 3 + 1


def read_month(path: str, month: dt.date) -> list[dict]:
    """Rows from the file whose expiry falls in the target month."""
    import csv
    out = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            raw = (r.get("ExpiryDate") or "").strip()
            if not raw:
                continue
            try:
                expiry = parse_date(raw)
            except Exception:
                continue
            if expiry.year == month.year and expiry.month == month.month:
                out.append(r)
    return out


def apply(conn, path: str, month: dt.date, reason: str) -> dict:
    with conn.cursor() as cur:
        # The seeded rules, not a copy of them.
        #
        # This used to carry its own is_excluded() described as mirroring the
        # seeded rules. It did not: the table holds seven renewals rules and the
        # copy covered five, missing SIG HIGH on both associate fields and the
        # Group1Abbrev rule entirely. One policy worth $640.00 passed the copy and
        # failed the real rules, so a month pinned by this script disagreed with
        # the same month imported normally -- silently, and in a script whose
        # whole purpose is establishing a baseline nobody can later correct.
        #
        # Loading the rules from the database is the only version that cannot
        # drift. Adding a rule through settings now takes effect here too, which
        # was the point of putting them in a table.
        engine = ExclusionEngine.load(cur, "renewals")
        rows = [r for r in read_month(path, month) if engine.check(r) is None]
        if not rows:
            raise SystemExit(f"no rows for {month:%B %Y} in {path}")

        cur.execute("""SELECT date_trunc('month', cut_off_date)::date
                       FROM reporting_settings WHERE id = 1""")
        cut_month = cur.fetchone()[0]

        # Clear whatever the month currently holds, including any pin, so this
        # is a deliberate re-establishment rather than an accumulation.
        cur.execute("DELETE FROM forecast_month_lock WHERE forecast_month = %s",
                    (month,))
        cur.execute("DELETE FROM original_forecast WHERE forecast_month = %s",
                    (month,))
        removed = cur.rowcount

        total = 0
        for r in rows:
            income = (dec(r.get("PrimaryAssocCommSum"))
                      + dec(r.get("PrimaryAssocCommTaxSum")))
            total += max(income, 0)
            cur.execute("""
                INSERT INTO original_forecast
                  (grain, policy_id, forecast_month, financial_year,
                   financial_quarter, origin, established_by, source_manager,
                   client_code, policy_number, class_abbrev,
                   expected_income, forecast_contribution, note)
                VALUES ('policy', %s, %s, %s, %s, 'snapshot', %s, %s, %s, %s, %s,
                        %s, GREATEST(%s, 0), %s)
                ON CONFLICT DO NOTHING""",
                        (r.get("PolicyID"), month, australian_fy(month),
                         australian_quarter(month), ACTOR,
                         (r.get("PolicyAccountManager") or "").strip(),
                         (r.get("ClientCode") or "").strip(),
                         (r.get("PolicyNumber") or "").strip(),
                         (r.get("ClassAbbrev") or r.get("PolicyClass") or "").strip(),
                         income, income,
                         f"Established from {Path(path).name}: {reason}"))

        cur.execute("""
            INSERT INTO forecast_month_lock
              (forecast_month, locked_by, reason, source_description, forecast_total)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (forecast_month) DO UPDATE SET
                locked_by = EXCLUDED.locked_by, reason = EXCLUDED.reason,
                source_description = EXCLUDED.source_description,
                forecast_total = EXCLUDED.forecast_total,
                active = true, locked_at = now()""",
                    (month, ACTOR, reason, Path(path).name, total))

        cur.execute("""
            UPDATE forecast_baseline
            SET baseline_source = %s, manager_exceptions = '[]'::jsonb, note = %s
            WHERE forecast_month = %s""",
                    # baseline_source is varchar(60); the full filename goes in
                    # the note, which is unbounded.
                    ("Renewals Pending (earlier extract)",
                     f"{month:%B %Y} was established from {Path(path).name}, an "
                     "earlier Renewals Pending extract taken while those renewals "
                     "were still pending. Pinned so a later snapshot cannot "
                     "overwrite it.",
                     month))
    conn.commit()
    return {"month": month, "policies": len(rows), "total": total,
            "removed": removed, "closed": month <= cut_month}


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    dsn, path = sys.argv[1], sys.argv[2]
    month = dt.date.fromisoformat(
        next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--month=")))
    reason = next((a.split("=", 1)[1] for a in sys.argv
                   if a.startswith("--reason=")), "established from an earlier extract")
    with psycopg2.connect(dsn) as conn:
        r = apply(conn, path, month, reason)
    print(f"{r['month']:%B %Y}: {r['policies']} policies, ${r['total']:,.2f} "
          f"(replaced {r['removed']} rows). Pinned against later snapshots.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
