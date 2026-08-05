"""Forecast-to-actual matching.

Three ideas keep this honest.

**Specific evidence first.** Client and policy number with an agreeing class and
a transaction date near expiry is stronger than the same match without the class
check, so it ranks above it. Class agreement raises confidence; it never lowers
it. A class *conflict* — both sides mapped, to different canonical classes —
disqualifies the top tier and the class-only tier, but never overrides matching
on client and policy number, which is the stronger identifier.

**Outcome and income are different questions.** Whether a policy renewed is not
the same as which dollars count towards renewal achievement. A lapse produces a
Lost Renewal outcome with zero Actual Renewal Income; its negative transaction
still reduces Net Actual Income and appears in Return Income, but it is never
presented as negative renewal income "achieved". Ordinary endorsements attached
to a renewed policy stay endorsement income.

**One transaction, one credit.** Distinct PolicyIDs legitimately share client,
policy number and expiry. Where several compete for one transaction, none is
credited automatically — they go to the review queue. Automatic allocation is
one-to-one by database constraint; apportionment is a deliberate manual act.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from psycopg2.extras import Json

ZERO = Decimal("0.00")

RENEWAL_CATEGORIES = ("RWL", "TRW")
LOSS_CATEGORIES = ("LAP",)
# Attached to a policy for the "total associated income" measure, but never
# counted as renewal income unless an invoice chain proves they correct the
# renewal itself.
ASSOCIATED_CATEGORIES = ("END", "ECN", "ADJ", "MCN", "NCN", "CCN")

MATCHABLE = RENEWAL_CATEGORIES + LOSS_CATEGORIES + ASSOCIATED_CATEGORIES

# tier -> (confidence, requires_review, description)
TIERS = {
    1: (Decimal("0.980"), False,
        "client + policy number + compatible class + date within tolerance"),
    2: (Decimal("0.900"), False,
        "client + policy number + date within tolerance"),
    3: (Decimal("0.750"), False,
        "client + policy number within the same financial year"),
    4: (Decimal("0.550"), True,
        "client + compatible class + date within tolerance"),
}


@dataclass
class MatchRunResult:
    run_id: int
    forecast_policies: int
    auto_matched: int
    auto_matched_income: Decimal
    review_queue: int
    unmatched_policies: int
    unmatched_actuals: int
    by_tier: dict
    by_outcome: dict
    renewals_without_forecast_coverage: int = 0
    renewals_without_forecast_income: Decimal = ZERO

    def render(self) -> str:
        lines = [
            f"Match run {self.run_id}",
            f"  Forecast policies in scope   {self.forecast_policies:>8,}",
            f"  Auto matched                 {self.auto_matched:>8,}",
            f"  Auto matched income          {self.auto_matched_income:>12,.2f}",
            f"  Review queue                 {self.review_queue:>8,}",
            f"  Unmatched forecast policies  {self.unmatched_policies:>8,}",
            f"  Unmatched actual renewals    {self.unmatched_actuals:>8,}",
            f"  Renewals outside match scope {self.renewals_without_forecast_coverage:>8,} "
            f"({self.renewals_without_forecast_income:,.2f})",
        ]
        if self.by_tier:
            lines.append("  By tier:")
            for tier in sorted(self.by_tier):
                d = self.by_tier[tier]
                lines.append(f"    Tier {tier} ({TIERS[int(tier)][0]:.0%})  "
                             f"{d['matches']:>6,} matches  {d['income']:>12,.2f}")
        if self.by_outcome:
            lines.append("  By outcome:")
            for k in sorted(self.by_outcome):
                lines.append(f"    {k:<22} {self.by_outcome[k]:>6,}")
        lines.append("  All income figures are GST inclusive.")
        return "\n".join(lines)


def _settings(cur) -> tuple[dt.date, int]:
    cur.execute("""SELECT cut_off_date, match_date_tolerance_days
                   FROM reporting_settings WHERE id = 1""")
    return cur.fetchone()


def run_matching(conn, run_by: str = "system:match", scope_month: dt.date | None = None,
                 clear_manual: bool = False) -> MatchRunResult:
    """Match forecast policies in completed periods against actual transactions.

    Only completed periods are matched: a future policy has not had its chance to
    renew and is Pending by definition, not Unmatched.

    Manual allocations and decisions are preserved across runs unless
    `clear_manual` is set, so re-running the matcher never silently discards a
    reviewer's work.
    """
    with conn.cursor() as cur:
        cut_off, tolerance = _settings(cur)
        cut_month = cut_off.replace(day=1)

        cur.execute("""
            INSERT INTO match_run (run_by, cut_off_date, date_tolerance_days)
            VALUES (%s, %s, %s) RETURNING id
        """, (run_by, cut_off, tolerance))
        run_id = cur.fetchone()[0]

        if clear_manual:
            cur.execute("DELETE FROM match_allocation")
        else:
            cur.execute("DELETE FROM match_allocation WHERE method = 'auto'")
        # Pending candidates are regenerated each run, so old ones are discarded
        # rather than piling up as 'superseded'. Rows a reviewer has accepted or
        # rejected are kept; the durable audit lives in match_decision.
        cur.execute("DELETE FROM match_candidate WHERE status IN ('pending','superseded')")
        # Outcomes are derived from allocations, so they are always recomputed.
        # What must survive a re-run is the reviewer's work, which lives in
        # match_allocation and match_decision, not here. Preserving outcome rows
        # instead left stale income behind whenever the underlying transactions
        # were rolled back: a policy showing renewal income with no allocation
        # to support it.
        cur.execute("DELETE FROM policy_outcome")

        month_filter = "AND p.forecast_month = %(scope_month)s" if scope_month else ""
        params = {"cut_month": cut_month, "cut_off": cut_off,
                  "tolerance": tolerance, "scope_month": scope_month}

        # --- candidate generation -------------------------------------------
        # Scope is the latest snapshot's policies in completed months, plus any
        # policy that was in the Original Forecast for a completed month even if
        # a later snapshot dropped it.
        cur.execute(f"""
            CREATE TEMP TABLE scope_policy ON COMMIT DROP AS
            SELECT DISTINCT ON (p.policy_id, p.forecast_month)
                   p.policy_id, p.forecast_month, p.client_code_norm,
                   p.policy_number_norm, p.expiry_date, p.source_manager,
                   ce.canonical_class,
                   p.forecast_contribution AS latest_contribution
            FROM forecast_policy p
            LEFT JOIN class_equivalence ce
                   ON ce.source_type = 'renewals'
                  AND ce.source_value = upper(trim(p.class_abbrev))
            WHERE NOT p.is_excluded
              AND p.forecast_month <= %(cut_month)s
              {month_filter}
            ORDER BY p.policy_id, p.forecast_month, p.snapshot_id DESC
        """, params)
        cur.execute("SELECT count(*) FROM scope_policy")
        policies_in_scope = cur.fetchone()[0]

        cur.execute("""
            CREATE TEMP TABLE scope_txn ON COMMIT DROP AS
            SELECT t.id, t.client_code_norm, t.policy_number_norm, t.transaction_date::date AS txn_date,
                   t.category, t.actual_income, t.invoice_number, t.financial_year,
                   ce.canonical_class
            FROM sales_transaction t
            LEFT JOIN class_equivalence ce
                   ON ce.source_type = 'sales'
                  AND ce.source_value = upper(trim(t.policy_class))
            WHERE NOT t.is_excluded AND t.category = ANY(%s)
        """, (list(MATCHABLE),))
        cur.execute("CREATE INDEX ON scope_txn (client_code_norm, policy_number_norm)")
        cur.execute("CREATE INDEX ON scope_policy (client_code_norm, policy_number_norm)")

        # Tier is assigned by the most specific evidence available. Class
        # agreement can only raise a match (tier 2 -> tier 1); a class conflict
        # blocks tier 1 and tier 4 but never demotes a policy-number match below
        # tier 3.
        cur.execute("""
            CREATE TEMP TABLE candidate ON COMMIT DROP AS
            WITH pairs AS (
                SELECT sp.policy_id, sp.forecast_month, st.id AS transaction_id,
                       st.category, st.actual_income, st.invoice_number,
                       sp.canonical_class AS policy_class_canon,
                       st.canonical_class  AS txn_class_canon,
                       (sp.policy_number_norm IS NOT NULL
                        AND sp.policy_number_norm = st.policy_number_norm) AS policy_number_match,
                       abs(st.txn_date - sp.expiry_date) <= %(tolerance)s AS within_tolerance,
                       (au_financial_year(sp.expiry_date) = st.financial_year) AS same_fy,
                       CASE
                         WHEN sp.canonical_class IS NULL OR st.canonical_class IS NULL
                           THEN 'unknown'
                         WHEN sp.canonical_class = st.canonical_class THEN 'compatible'
                         ELSE 'conflict'
                       END AS class_agreement
                FROM scope_policy sp
                JOIN scope_txn st ON st.client_code_norm = sp.client_code_norm
                WHERE sp.client_code_norm IS NOT NULL AND sp.client_code_norm <> ''
            )
            SELECT *,
                   CASE
                     WHEN policy_number_match AND within_tolerance
                          AND class_agreement = 'compatible'          THEN 1
                     WHEN policy_number_match AND within_tolerance     THEN 2
                     WHEN policy_number_match AND same_fy              THEN 3
                     WHEN NOT policy_number_match AND within_tolerance
                          AND class_agreement = 'compatible'          THEN 4
                     ELSE NULL
                   END AS tier
            FROM pairs
        """, params)
        cur.execute("DELETE FROM candidate WHERE tier IS NULL")

        # --- contention resolution ------------------------------------------
        # Best tier per transaction. If more than one policy sits at that tier,
        # nothing is credited automatically.
        cur.execute("""
            CREATE TEMP TABLE txn_best ON COMMIT DROP AS
            SELECT transaction_id, MIN(tier) AS best_tier,
                   COUNT(DISTINCT policy_id) FILTER (
                       WHERE tier = (SELECT MIN(c2.tier) FROM candidate c2
                                     WHERE c2.transaction_id = c.transaction_id)
                   ) AS competing_policies
            FROM candidate c GROUP BY transaction_id
        """)

        # Transactions already allocated manually are left alone.
        cur.execute("""
            INSERT INTO match_allocation
              (transaction_id, policy_id, forecast_month, allocated_income,
               is_renewal_income, allocation_basis, method, tier, confidence, created_by)
            SELECT c.transaction_id, c.policy_id, c.forecast_month, c.actual_income,
                   c.category = ANY(%(renewal)s),
                   'tier ' || c.tier || ': ' || CASE c.tier
                        WHEN 1 THEN 'client + policy number + compatible class + date'
                        WHEN 2 THEN 'client + policy number + date'
                        WHEN 3 THEN 'client + policy number, same financial year'
                        ELSE 'client + compatible class + date' END,
                   'auto', c.tier,
                   CASE c.tier WHEN 1 THEN 0.980 WHEN 2 THEN 0.900
                               WHEN 3 THEN 0.750 ELSE 0.550 END,
                   %(run_by)s
            FROM candidate c
            JOIN txn_best b ON b.transaction_id = c.transaction_id
                           AND b.best_tier = c.tier
            WHERE b.competing_policies = 1
              AND c.tier < 4
              AND NOT EXISTS (SELECT 1 FROM match_allocation ma
                              WHERE ma.transaction_id = c.transaction_id)
        """, {"renewal": list(RENEWAL_CATEGORIES), "run_by": run_by})
        auto_matched = cur.rowcount

        # --- review queue ----------------------------------------------------
        cur.execute("""
            INSERT INTO match_candidate
              (transaction_id, policy_id, forecast_month, tier, confidence, reason,
               candidate_rank, detail)
            SELECT c.transaction_id, c.policy_id, c.forecast_month, c.tier,
                   CASE c.tier WHEN 1 THEN 0.980 WHEN 2 THEN 0.900
                               WHEN 3 THEN 0.750 ELSE 0.550 END,
                   CASE WHEN b.competing_policies > 1
                          THEN 'multiple_policies_for_transaction'
                        WHEN c.tier = 4 THEN 'low_tier_requires_review'
                        ELSE 'class_conflict' END,
                   row_number() OVER (PARTITION BY c.transaction_id ORDER BY c.tier),
                   jsonb_build_object('class_agreement', c.class_agreement,
                                      'category', c.category,
                                      'income', c.actual_income)
            FROM candidate c
            JOIN txn_best b ON b.transaction_id = c.transaction_id
                           AND b.best_tier = c.tier
            WHERE (b.competing_policies > 1 OR c.tier = 4)
              AND NOT EXISTS (SELECT 1 FROM match_allocation ma
                              WHERE ma.transaction_id = c.transaction_id)
        """)
        review_queue = cur.rowcount

        # --- invoice chain: adjustments that genuinely correct the renewal ----
        # An ADJ, END or ECN counts as renewal income only where it shares an
        # invoice number with a renewal transaction allocated to the same policy.
        cur.execute("""
            UPDATE match_allocation a SET is_renewal_income = true,
                   allocation_basis = a.allocation_basis || '; invoice chain to renewal'
            FROM sales_transaction t
            WHERE a.transaction_id = t.id
              AND NOT a.is_renewal_income
              AND t.category IN ('ADJ','END','ECN')
              AND t.invoice_number IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM match_allocation a2
                  JOIN sales_transaction t2 ON t2.id = a2.transaction_id
                  WHERE a2.policy_id = a.policy_id
                    AND a2.forecast_month = a.forecast_month
                    AND t2.category = ANY(%s)
                    AND t2.invoice_number = t.invoice_number)
        """, (list(RENEWAL_CATEGORIES),))
        invoice_chained = cur.rowcount

        # --- outcomes ---------------------------------------------------------
        cur.execute("""
            INSERT INTO policy_outcome
              (policy_id, forecast_month, canonical_manager, outcome,
               renewal_transaction_income, total_associated_income,
               original_forecast_income, latest_forecast_income,
               matched_transaction_count, best_tier, confidence, requires_review,
               is_manual)
            SELECT sp.policy_id, sp.forecast_month,
                   COALESCE(mr.canonical_manager, sp.source_manager),
                   CASE
                     WHEN EXISTS (SELECT 1 FROM match_allocation a
                                  JOIN sales_transaction t ON t.id = a.transaction_id
                                  WHERE a.policy_id = sp.policy_id
                                    AND a.forecast_month = sp.forecast_month
                                    AND t.category = 'RWL') THEN 'renewed'
                     WHEN EXISTS (SELECT 1 FROM match_allocation a
                                  JOIN sales_transaction t ON t.id = a.transaction_id
                                  WHERE a.policy_id = sp.policy_id
                                    AND a.forecast_month = sp.forecast_month
                                    AND t.category = 'TRW') THEN 'transfer_renewed'
                     WHEN EXISTS (SELECT 1 FROM match_allocation a
                                  JOIN sales_transaction t ON t.id = a.transaction_id
                                  WHERE a.policy_id = sp.policy_id
                                    AND a.forecast_month = sp.forecast_month
                                    AND t.category = 'LAP') THEN 'lapsed_lost'
                     WHEN EXISTS (SELECT 1 FROM match_candidate mc
                                  WHERE mc.policy_id = sp.policy_id
                                    AND mc.status = 'pending') THEN 'multiple_candidates'
                     WHEN EXISTS (SELECT 1 FROM forecast_movement fm
                                  WHERE fm.policy_id = sp.policy_id
                                    AND fm.removed) THEN 'removed_from_latest'
                     WHEN sp.forecast_month > %(cut_month)s THEN 'pending'
                     -- The renewal window is still open. A policy expiring days
                     -- before the cut-off has not failed to renew; it has not
                     -- been processed yet. Calling that 'unmatched' would
                     -- manufacture lapses at every period end.
                     WHEN sp.expiry_date > %(cut_off)s - %(tolerance)s * INTERVAL '1 day'
                       THEN 'pending'
                     ELSE 'unmatched'
                   END,
                   -- Renewal income: RWL and TRW, plus invoice-chained
                   -- corrections. A lapse contributes zero here by construction.
                   COALESCE((SELECT SUM(a.allocated_income) FROM match_allocation a
                             WHERE a.policy_id = sp.policy_id
                               AND a.forecast_month = sp.forecast_month
                               AND a.is_renewal_income), 0),
                   COALESCE((SELECT SUM(a.allocated_income) FROM match_allocation a
                             WHERE a.policy_id = sp.policy_id
                               AND a.forecast_month = sp.forecast_month), 0),
                   COALESCE(og.forecast_contribution, 0),
                   sp.latest_contribution,
                   COALESCE((SELECT count(*) FROM match_allocation a
                             WHERE a.policy_id = sp.policy_id
                               AND a.forecast_month = sp.forecast_month), 0),
                   (SELECT MIN(a.tier) FROM match_allocation a
                    WHERE a.policy_id = sp.policy_id
                      AND a.forecast_month = sp.forecast_month),
                   (SELECT MAX(a.confidence) FROM match_allocation a
                    WHERE a.policy_id = sp.policy_id
                      AND a.forecast_month = sp.forecast_month),
                   EXISTS (SELECT 1 FROM match_candidate mc
                           WHERE mc.policy_id = sp.policy_id AND mc.status = 'pending'),
                   EXISTS (SELECT 1 FROM match_allocation a
                           WHERE a.policy_id = sp.policy_id
                             AND a.forecast_month = sp.forecast_month
                             AND a.method = 'manual')
            FROM scope_policy sp
            LEFT JOIN v_manager_resolution mr ON mr.source_manager = sp.source_manager
            LEFT JOIN original_forecast og
                   ON og.policy_id = sp.policy_id
                  AND og.forecast_month = sp.forecast_month
                  AND og.grain = 'policy'
            ON CONFLICT (policy_id, forecast_month) DO NOTHING
        """, params)

        # A lapse never presents as negative renewal income achieved. The
        # negative transaction still reduces Net Actual Income and shows in
        # Return Income; it just is not renewal income.
        cur.execute("""
            UPDATE policy_outcome SET renewal_transaction_income = 0
            WHERE outcome = 'lapsed_lost' AND renewal_transaction_income < 0
        """)
        lapse_zeroed = cur.rowcount

        cur.execute("""
            UPDATE policy_outcome SET outcome = 'manually_resolved'
            WHERE is_manual AND outcome NOT IN ('renewed','transfer_renewed','lapsed_lost')
        """)

        # --- unmatched actual renewals ---------------------------------------
        cur.execute("""
            INSERT INTO match_candidate
              (transaction_id, policy_id, forecast_month, reason, detail)
            SELECT t.id, NULL, date_trunc('month', t.transaction_date)::date,
                   'unmatched_actual_renewal',
                   jsonb_build_object('client_code', t.client_code,
                                      'policy_number', t.policy_number,
                                      'category', t.category,
                                      'income', t.actual_income)
            FROM sales_transaction t
            WHERE NOT t.is_excluded
              AND t.category = ANY(%(renewal)s)
              AND date_trunc('month', t.transaction_date)::date <= %(cut_month)s
              AND NOT EXISTS (SELECT 1 FROM match_allocation a
                              WHERE a.transaction_id = t.id)
              -- Only months that actually have a policy-grain forecast can
              -- produce an unmatched renewal. FY2025-26 has no policy-level
              -- forecast at all, so its 8,600-odd renewals are not match
              -- failures -- there was nothing to match them against. Queuing
              -- them would bury the real exceptions.
              AND EXISTS (SELECT 1 FROM forecast_policy fp
                          WHERE NOT fp.is_excluded
                            AND fp.forecast_month = date_trunc('month',
                                                    t.transaction_date)::date)
        """, {"renewal": list(RENEWAL_CATEGORIES), "cut_month": cut_month})
        unmatched_actuals = cur.rowcount

        cur.execute("""
            SELECT count(*), COALESCE(SUM(actual_income), 0) FROM sales_transaction t
            WHERE NOT t.is_excluded AND t.category = ANY(%(renewal)s)
              AND date_trunc('month', t.transaction_date)::date <= %(cut_month)s
              AND NOT EXISTS (SELECT 1 FROM forecast_policy fp
                              WHERE NOT fp.is_excluded
                                AND fp.forecast_month = date_trunc('month',
                                                        t.transaction_date)::date)
        """, {"renewal": list(RENEWAL_CATEGORIES), "cut_month": cut_month})
        no_coverage_count, no_coverage_income = cur.fetchone()

        cur.execute("""
            INSERT INTO match_candidate (policy_id, forecast_month, reason, detail)
            SELECT po.policy_id, po.forecast_month, 'unmatched_forecast_policy',
                   jsonb_build_object('original_forecast', po.original_forecast_income)
            FROM policy_outcome po WHERE po.outcome = 'unmatched'
        """)
        unmatched_policies = cur.rowcount

        # --- summary ----------------------------------------------------------
        cur.execute("""
            SELECT tier, count(*), COALESCE(SUM(allocated_income), 0)
            FROM match_allocation WHERE method='auto' GROUP BY tier ORDER BY tier
        """)
        by_tier = {str(t): {"matches": n, "income": float(inc)}
                   for t, n, inc in cur.fetchall()}
        cur.execute("SELECT outcome, count(*) FROM policy_outcome GROUP BY 1")
        by_outcome = dict(cur.fetchall())
        cur.execute("""SELECT COALESCE(SUM(allocated_income),0) FROM match_allocation
                       WHERE method='auto' AND is_renewal_income""")
        matched_income = cur.fetchone()[0]

        cur.execute("""
            UPDATE match_run SET forecast_policies=%s, auto_matched=%s,
                auto_matched_income=%s, review_queue=%s, unmatched_policies=%s,
                unmatched_actuals=%s, by_tier=%s,
                note=%s
            WHERE id=%s
        """, (policies_in_scope, auto_matched, matched_income, review_queue,
              unmatched_policies, unmatched_actuals, Json(by_tier),
              f"{invoice_chained} adjustment(s) linked to a renewal by invoice chain; "
              f"{lapse_zeroed} lapse outcome(s) held at zero renewal income; "
              f"{no_coverage_count} renewal transaction(s) ({no_coverage_income:,.2f}) "
              f"fall in months with no policy-grain forecast and are outside "
              f"matching scope",
              run_id))
    conn.commit()

    return MatchRunResult(
        run_id=run_id, forecast_policies=policies_in_scope, auto_matched=auto_matched,
        auto_matched_income=matched_income, review_queue=review_queue,
        unmatched_policies=unmatched_policies, unmatched_actuals=unmatched_actuals,
        by_tier=by_tier, by_outcome=by_outcome,
        renewals_without_forecast_coverage=no_coverage_count,
        renewals_without_forecast_income=no_coverage_income)


# --- manual review -----------------------------------------------------------

def _snapshot_decision(cur, policy_id, forecast_month, transaction_id) -> dict | None:
    cur.execute("""
        SELECT policy_id, forecast_month, transaction_id, allocated_income,
               is_renewal_income, method, tier, confidence, allocation_basis
        FROM match_allocation
        WHERE transaction_id = %s AND (%s::bigint IS NULL OR policy_id = %s)
    """, (transaction_id, policy_id, policy_id))
    rows = cur.fetchall()
    if not rows:
        return None
    cols = ("policy_id", "forecast_month", "transaction_id", "allocated_income",
            "is_renewal_income", "method", "tier", "confidence", "allocation_basis")
    return {"allocations": [dict(zip(cols, [str(v) for v in r])) for r in rows]}


def manual_match(conn, policy_id: int, forecast_month: dt.date, transaction_id: int,
                 reviewer: str, reason: str, allocated_income: Decimal | None = None,
                 is_renewal_income: bool | None = None) -> dict:
    """Attach a transaction to a policy by hand, replacing any auto allocation."""
    with conn.cursor() as cur:
        previous = _snapshot_decision(cur, None, forecast_month, transaction_id)
        cur.execute("""SELECT actual_income, category FROM sales_transaction
                       WHERE id = %s""", (transaction_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f"transaction {transaction_id} not found")
        income, category = row
        amount = allocated_income if allocated_income is not None else income
        renewal = (is_renewal_income if is_renewal_income is not None
                   else category in RENEWAL_CATEGORIES)

        cur.execute("DELETE FROM match_allocation WHERE transaction_id = %s",
                    (transaction_id,))
        cur.execute("""
            INSERT INTO match_allocation
              (transaction_id, policy_id, forecast_month, allocated_income,
               is_renewal_income, allocation_basis, method, confidence, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,'manual',1.000,%s)
        """, (transaction_id, policy_id, forecast_month, amount, renewal,
              f"manual match by {reviewer}", reviewer))

        cur.execute("""UPDATE match_candidate SET status='accepted'
                       WHERE transaction_id=%s AND policy_id=%s AND status='pending'""",
                    (transaction_id, policy_id))
        cur.execute("""UPDATE match_candidate SET status='rejected'
                       WHERE transaction_id=%s AND policy_id<>%s AND status='pending'""",
                    (transaction_id, policy_id))

        new = _snapshot_decision(cur, policy_id, forecast_month, transaction_id)
        cur.execute("""
            INSERT INTO match_decision
              (policy_id, forecast_month, transaction_id, action, previous_decision,
               new_decision, reason, reviewer)
            VALUES (%s,%s,%s,'manual_match',%s,%s,%s,%s)
        """, (policy_id, forecast_month, transaction_id,
              Json(previous) if previous else None, Json(new), reason, reviewer))
        _recompute_outcome(cur, policy_id, forecast_month, manual=True)
    conn.commit()
    return {"policy_id": policy_id, "transaction_id": transaction_id,
            "previous": previous, "reviewer": reviewer}


def reject_match(conn, transaction_id: int, reviewer: str, reason: str,
                 policy_id: int | None = None) -> dict:
    """Remove an allocation and record why."""
    with conn.cursor() as cur:
        previous = _snapshot_decision(cur, policy_id, None, transaction_id)
        cur.execute("""SELECT policy_id, forecast_month FROM match_allocation
                       WHERE transaction_id=%s""", (transaction_id,))
        affected = cur.fetchall()
        cur.execute("DELETE FROM match_allocation WHERE transaction_id=%s", (transaction_id,))
        cur.execute("""UPDATE match_candidate SET status='rejected'
                       WHERE transaction_id=%s AND status='pending'""", (transaction_id,))
        cur.execute("""
            INSERT INTO match_decision
              (policy_id, forecast_month, transaction_id, action, previous_decision,
               new_decision, reason, reviewer)
            VALUES (%s,NULL,%s,'reject_match',%s,NULL,%s,%s)
        """, (policy_id, transaction_id, Json(previous) if previous else None,
              reason, reviewer))
        for pid, month in affected:
            _recompute_outcome(cur, pid, month, manual=True)
    conn.commit()
    return {"transaction_id": transaction_id, "removed": len(affected),
            "previous": previous}


def apportion(conn, transaction_id: int, splits: list[tuple[int, dt.date, Decimal]],
              reviewer: str, reason: str) -> dict:
    """Split one transaction across several policies with explicit amounts.

    The database trigger rejects the whole apportionment if the parts exceed the
    transaction, so a split can never inflate reported income.
    """
    with conn.cursor() as cur:
        previous = _snapshot_decision(cur, None, None, transaction_id)
        cur.execute("""SELECT actual_income, category FROM sales_transaction WHERE id=%s""",
                    (transaction_id,))
        income, category = cur.fetchone()
        cur.execute("DELETE FROM match_allocation WHERE transaction_id=%s", (transaction_id,))
        for policy_id, month, amount in splits:
            cur.execute("""
                INSERT INTO match_allocation
                  (transaction_id, policy_id, forecast_month, allocated_income,
                   is_renewal_income, allocation_basis, method, confidence, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,'manual',1.000,%s)
            """, (transaction_id, policy_id, month, amount,
                  category in RENEWAL_CATEGORIES,
                  f"manual apportionment by {reviewer}", reviewer))
        new = _snapshot_decision(cur, None, None, transaction_id)
        cur.execute("""
            INSERT INTO match_decision
              (policy_id, forecast_month, transaction_id, action, previous_decision,
               new_decision, reason, reviewer)
            VALUES (NULL,NULL,%s,'apportion',%s,%s,%s,%s)
        """, (transaction_id, Json(previous) if previous else None, Json(new),
              reason, reviewer))
        for policy_id, month, _ in splits:
            _recompute_outcome(cur, policy_id, month, manual=True)
    conn.commit()
    return {"transaction_id": transaction_id, "splits": len(splits),
            "transaction_income": income}


def _recompute_outcome(cur, policy_id: int, forecast_month: dt.date,
                       manual: bool = False) -> None:
    cur.execute("""
        UPDATE policy_outcome po SET
          renewal_transaction_income = COALESCE((
              SELECT SUM(a.allocated_income) FROM match_allocation a
              WHERE a.policy_id = po.policy_id AND a.forecast_month = po.forecast_month
                AND a.is_renewal_income), 0),
          total_associated_income = COALESCE((
              SELECT SUM(a.allocated_income) FROM match_allocation a
              WHERE a.policy_id = po.policy_id AND a.forecast_month = po.forecast_month), 0),
          matched_transaction_count = COALESCE((
              SELECT count(*) FROM match_allocation a
              WHERE a.policy_id = po.policy_id AND a.forecast_month = po.forecast_month), 0),
          outcome = CASE
            WHEN EXISTS (SELECT 1 FROM match_allocation a
                         JOIN sales_transaction t ON t.id = a.transaction_id
                         WHERE a.policy_id = po.policy_id
                           AND a.forecast_month = po.forecast_month
                           AND t.category = 'RWL') THEN 'renewed'
            WHEN EXISTS (SELECT 1 FROM match_allocation a
                         JOIN sales_transaction t ON t.id = a.transaction_id
                         WHERE a.policy_id = po.policy_id
                           AND a.forecast_month = po.forecast_month
                           AND t.category = 'TRW') THEN 'transfer_renewed'
            WHEN EXISTS (SELECT 1 FROM match_allocation a
                         JOIN sales_transaction t ON t.id = a.transaction_id
                         WHERE a.policy_id = po.policy_id
                           AND a.forecast_month = po.forecast_month
                           AND t.category = 'LAP') THEN 'lapsed_lost'
            ELSE 'manually_resolved' END,
          is_manual = %s,
          requires_review = EXISTS (SELECT 1 FROM match_candidate mc
                                    WHERE mc.policy_id = po.policy_id
                                      AND mc.status = 'pending'),
          computed_at = now()
        WHERE po.policy_id = %s AND po.forecast_month = %s
    """, (manual, policy_id, forecast_month))
    cur.execute("""UPDATE policy_outcome SET renewal_transaction_income = 0
                   WHERE policy_id=%s AND forecast_month=%s
                     AND outcome='lapsed_lost' AND renewal_transaction_income < 0""",
                (policy_id, forecast_month))
