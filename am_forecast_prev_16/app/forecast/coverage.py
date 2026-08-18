"""Snapshot coverage analysis.

A month absent from a newer Renewals Pending file is ambiguous. It might mean
every policy in that month has gone, or it might mean the export was filtered,
narrower, or uploaded out of order. Treating absence as removal is the dangerous
reading: one bad file would wipe an otherwise valid Latest Forecast.

So coverage is declared, not assumed. The preview shows which months the file
covers, which months the previous snapshot had that this one does not, and what
would be removed if the file were accepted as complete. A mass removal requires
explicit confirmation before accept.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from decimal import Decimal

ZERO = Decimal("0.00")

# A month losing more than this share of its policies is treated as a mass
# removal and blocks accept until confirmed. Real attrition between two
# consecutive pending reports is small; a cliff of this size is nearly always a
# narrower export.
MASS_REMOVAL_POLICY_SHARE = Decimal("0.50")


@dataclass
class MonthCoverage:
    forecast_month: dt.date
    policy_count: int
    forecast_contribution: Decimal
    previous_policy_count: int = 0
    previous_contribution: Decimal = ZERO
    is_present: bool = True
    would_remove_policies: int = 0
    would_remove_income: Decimal = ZERO
    is_mass_removal: bool = False


@dataclass
class CoverageReport:
    months: list[MonthCoverage] = field(default_factory=list)
    absent_months: list[MonthCoverage] = field(default_factory=list)
    requires_confirmation: bool = False
    warnings: list[str] = field(default_factory=list)
    cut_off_stale: bool = False

    def render(self) -> str:
        lines = ["  Month coverage:"]
        for m in self.months:
            flag = "  MASS REMOVAL" if m.is_mass_removal else ""
            lines.append(
                f"    {m.forecast_month:%b %Y}  {m.policy_count:>5,} policies "
                f"{m.forecast_contribution:>12,.2f}"
                + (f"   would remove {m.would_remove_policies:>4,} "
                   f"({m.would_remove_income:,.2f}){flag}"
                   if m.would_remove_policies else ""))
        for m in self.absent_months:
            lines.append(
                f"    {m.forecast_month:%b %Y}  ABSENT from this file  "
                f"(previous snapshot held {m.previous_policy_count:,} policies, "
                f"{m.previous_contribution:,.2f})")
        for w in self.warnings:
            lines.append(f"  ! {w}")
        return "\n".join(lines)


def record_coverage(cur, snapshot_id: int, confirmed_months: list[dt.date] | None = None
                    ) -> None:
    """Write observed month coverage for a snapshot.

    `confirmed_months` are the months the uploader has confirmed the file covers
    in full. Only those participate in removal comparison.
    """
    confirmed = set(confirmed_months or [])
    cur.execute("""
        INSERT INTO snapshot_month_coverage
          (snapshot_id, forecast_month, policy_count, forecast_contribution,
           is_confirmed_complete, coverage_basis)
        SELECT %s, forecast_month, count(*), COALESCE(SUM(forecast_contribution), 0),
               forecast_month = ANY(%s), 'observed'
        FROM forecast_policy
        WHERE snapshot_id = %s AND NOT is_excluded
        GROUP BY forecast_month
        ON CONFLICT (snapshot_id, forecast_month) DO UPDATE SET
          policy_count = EXCLUDED.policy_count,
          forecast_contribution = EXCLUDED.forecast_contribution,
          is_confirmed_complete = EXCLUDED.is_confirmed_complete
    """, (snapshot_id, list(confirmed), snapshot_id))


def confirm_months(cur, snapshot_id: int, months: list[dt.date], confirmed_by: str,
                   note: str | None = None) -> int:
    cur.execute("""
        UPDATE snapshot_month_coverage
        SET is_confirmed_complete = true, coverage_basis = 'confirmed_by_user'
        WHERE snapshot_id = %s AND forecast_month = ANY(%s)
    """, (snapshot_id, months))
    return cur.rowcount


def analyse_staged_coverage(cur, batch_id: int) -> CoverageReport:
    """Compare a staged renewals batch against the current Latest Forecast."""
    report = CoverageReport()

    cur.execute("""SELECT date_trunc('month', cut_off_date)::date, cut_off_date
                   FROM reporting_settings WHERE id = 1""")
    cut_month, cut_off = cur.fetchone()

    cur.execute("""
        SELECT period_month, count(*), COALESCE(SUM(forecast_contribution), 0)
        FROM import_staging
        WHERE batch_id = %s AND status IN ('valid', 'excluded') AND NOT is_excluded
        GROUP BY 1 ORDER BY 1
    """, (batch_id,))
    staged = {m: (n, amt) for m, n, amt in cur.fetchall()}

    cur.execute("""
        SELECT forecast_month, count(*), COALESCE(SUM(forecast_contribution), 0)
        FROM v_latest_forecast_policy GROUP BY 1 ORDER BY 1
    """)
    current = {m: (n, amt) for m, n, amt in cur.fetchall()}

    # Staged policy ids per month, to size the real removal rather than guess
    # from counts alone.
    cur.execute("""
        SELECT period_month, array_agg(policy_id) FROM import_staging
        WHERE batch_id = %s AND status IN ('valid','excluded') AND NOT is_excluded
        GROUP BY 1
    """, (batch_id,))
    staged_ids = {m: set(ids) for m, ids in cur.fetchall()}

    for month in sorted(set(staged) | set(current)):
        n, amt = staged.get(month, (0, ZERO))
        pn, pamt = current.get(month, (0, ZERO))
        mc = MonthCoverage(forecast_month=month, policy_count=n,
                           forecast_contribution=amt,
                           previous_policy_count=pn, previous_contribution=pamt,
                           is_present=month in staged)
        if month in staged and month in current and month > cut_month:
            cur.execute("""
                SELECT count(*), COALESCE(SUM(forecast_contribution), 0)
                FROM v_latest_forecast_policy
                WHERE forecast_month = %s AND NOT (policy_id = ANY(%s))
            """, (month, list(staged_ids.get(month, set()))))
            mc.would_remove_policies, mc.would_remove_income = cur.fetchone()
            if pn and Decimal(mc.would_remove_policies) / Decimal(pn) > MASS_REMOVAL_POLICY_SHARE:
                mc.is_mass_removal = True
                report.requires_confirmation = True
                report.warnings.append(
                    f"{month:%b %Y}: accepting this file would remove "
                    f"{mc.would_remove_policies:,} of {pn:,} policies "
                    f"({mc.would_remove_income:,.2f}). That is a mass removal and "
                    "usually means a narrower export rather than real attrition. "
                    "Confirm the month is covered in full before accepting.")
        if month not in staged:
            report.absent_months.append(mc)
            if month > cut_month:
                report.requires_confirmation = True
                report.warnings.append(
                    f"{month:%b %Y} is absent from this file but the current Latest "
                    f"Forecast holds {pn:,} policies ({pamt:,.2f}). It will be left "
                    "untouched, not removed. Confirm the month explicitly if this "
                    "file really does cover it.")
        else:
            report.months.append(mc)

    # A snapshot taken after a month has completed cannot be compared against a
    # cut-off that still sits before it.
    if staged:
        earliest = min(staged)
        if earliest <= cut_month and any(m > cut_month for m in staged):
            cur.execute("""SELECT max(transaction_date)::date FROM sales_transaction""")
            latest_txn = cur.fetchone()[0]
            if latest_txn and latest_txn.replace(day=1) > cut_month:
                report.cut_off_stale = True
                report.warnings.append(
                    f"Reporting Cut-Off Date is {cut_off} but actual transactions run "
                    f"to {latest_txn}. Update the cut-off before accepting, or a "
                    "completed month will be compared as though it were still open.")
                report.requires_confirmation = True

    return report
