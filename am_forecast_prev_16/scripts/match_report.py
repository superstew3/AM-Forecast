#!/usr/bin/env python3
"""Stage 4 match reporting.

    python scripts/match_report.py <dsn> [--run] [--user=NAME]

`--run` re-runs the matcher first. Without it the last run's results are
reported.
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.matching import run_matching  # noqa: E402

SECTIONS: list[tuple[str, str]] = [
    ("Run summary", """
        SELECT id, run_at::date AS run_date, run_by, cut_off_date,
               date_tolerance_days AS tol, forecast_policies, auto_matched,
               to_char(auto_matched_income,'FM999,999,990.00') AS matched_income,
               review_queue, unmatched_policies, unmatched_actuals
        FROM match_run ORDER BY id DESC LIMIT 1"""),

    ("Matches by confidence tier", """
        SELECT tier, tier_description, method, allocations, policies, transactions,
               to_char(allocated_income,'FM999,999,990.00') AS allocated,
               to_char(renewal_income,'FM999,999,990.00') AS renewal_income,
               max_confidence AS confidence
        FROM v_match_tier_summary ORDER BY tier NULLS LAST, method"""),

    ("Policy outcomes", """
        SELECT outcome, count(*) AS policies,
               to_char(SUM(original_forecast_income),'FM999,999,990.00') AS original_forecast,
               to_char(SUM(renewal_transaction_income),'FM999,999,990.00') AS renewal_income,
               to_char(SUM(total_associated_income),'FM999,999,990.00') AS total_associated
        FROM policy_outcome GROUP BY 1 ORDER BY 2 DESC"""),

    ("Review queue", """
        SELECT reason, status, count(*) AS records
        FROM match_candidate GROUP BY 1,2 ORDER BY 1,2"""),

    ("Ambiguous matches awaiting decision", """
        SELECT transaction_id, count(*) AS competing_policies,
               array_agg(policy_id) AS policy_ids, min(tier) AS best_tier
        FROM match_candidate
        WHERE reason='multiple_policies_for_transaction' AND status='pending'
        GROUP BY 1 ORDER BY 2 DESC LIMIT 20"""),

    ("Unmatched forecast policies", """
        SELECT po.policy_id, pr.client_code, pr.policy_number, pr.class_abbrev,
               pr.expiry_date,
               to_char(po.original_forecast_income,'FM99,990.00') AS original_forecast,
               po.canonical_manager
        FROM policy_outcome po
        JOIN v_policy_renewal pr ON pr.policy_id = po.policy_id
                                AND pr.forecast_month = po.forecast_month
        WHERE po.outcome = 'unmatched' ORDER BY po.original_forecast_income DESC LIMIT 20"""),

    ("Unmatched actual renewals (months that do have a forecast)", """
        SELECT date_trunc('month', t.transaction_date)::date AS month,
               count(*) AS transactions,
               to_char(SUM(t.actual_income),'FM999,999,990.00') AS income
        FROM sales_transaction t
        JOIN match_candidate mc ON mc.transaction_id = t.id
                               AND mc.reason='unmatched_actual_renewal'
                               AND mc.status='pending'
        GROUP BY 1 ORDER BY 1"""),

    ("Duplicate-allocation control", """
        SELECT status, count(*) AS transactions,
               max(policies_credited) AS max_policies_per_transaction,
               max(auto_allocations) AS max_auto_allocations
        FROM v_allocation_integrity GROUP BY 1"""),

    ("Forecast against actual renewal income, by manager", """
        SELECT canonical_manager, forecast_month,
               to_char(original_forecast,'FM999,990.00') AS original_forecast,
               to_char(actual_renewal_income,'FM999,990.00') AS actual_renewal_income,
               COALESCE(to_char(renewal_achievement*100,'FM990.0')||'%','N/A') AS achievement,
               policies_renewed AS renewed, policies_transferred AS transferred,
               policies_lapsed AS lapsed, policies_pending AS pending,
               policies_unresolved AS unresolved,
               COALESCE(to_char(retention_by_income*100,'FM990.0')||'%','N/A') AS retention_income
        FROM v_renewal_outcome_performance
        WHERE original_forecast > 0 OR actual_renewal_income <> 0
        ORDER BY forecast_month, original_forecast DESC"""),

    ("Manual decision history", """
        SELECT decided_at::date AS decided, reviewer, action, policy_id, transaction_id,
               left(reason, 50) AS reason,
               (previous_decision IS NOT NULL) AS replaced_a_prior_decision
        FROM v_match_decision_history LIMIT 25"""),
]


def render(cur, title: str, sql: str) -> str:
    cur.execute(sql)
    cols = [d.name for d in cur.description]
    data = cur.fetchall()
    if not data:
        return f"\n## {title}\n\n(no rows)\n"
    widths = [max(len(c), *(len(str(r[i]) if r[i] is not None else "") for r in data))
              for i, c in enumerate(cols)]
    head = "  ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
    rule = "  ".join("-" * w for w in widths)
    body = "\n".join("  ".join(
        (str(r[i]) if r[i] is not None else "").ljust(widths[i])
        for i in range(len(cols))) for r in data)
    return f"\n## {title}\n\n{head}\n{rule}\n{body}\n"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dsn = sys.argv[1]
    user = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--user=")),
                "system:report")
    with psycopg2.connect(dsn) as conn:
        if "--run" in sys.argv:
            result = run_matching(conn, user)
            print(result.render())
        with conn.cursor() as cur:
            cur.execute("SELECT cut_off_date FROM reporting_settings WHERE id=1")
            print(f"\nReporting cut-off: {cur.fetchone()[0]}")
            print("All income figures are GST inclusive.")
            for title, sql in SECTIONS:
                print(render(cur, title, sql))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
