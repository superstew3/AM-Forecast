"""Forecast movement: Original to Latest.

A newer Renewals Pending snapshot changes the Latest Forecast only. The Original
Forecast, once established for a month, is frozen. The difference between them
is not a correction to be absorbed — it is the reportable fact that the renewal
book has moved, and it is recorded policy by policy.

The rule that shapes everything here: a policy that disappears from a newer
snapshot must not create negative forecast income. Its expected income is
removed from Latest and the removal is recorded as a movement. A monthly Latest
total can therefore fall, but it can never go below zero.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

DETAIL_FIELDS = ("class_abbrev", "underwriter_abbrev", "policy_number", "client_code",
                 "expiry_date")

ZERO = Decimal("0.00")


def _cut_off_month(cur) -> dt.date:
    cur.execute("SELECT date_trunc('month', cut_off_date)::date FROM reporting_settings WHERE id=1")
    return cur.fetchone()[0]


def previous_snapshot_for(cur, snapshot_id: int) -> int | None:
    """The newest accepted snapshot before this one."""
    cur.execute("""
        SELECT s.id FROM forecast_snapshot s
        JOIN upload_batch b ON b.id = s.batch_id AND b.status = 'accepted'
        WHERE s.id < %s ORDER BY s.id DESC LIMIT 1
    """, (snapshot_id,))
    row = cur.fetchone()
    return row[0] if row else None


def compute_movement(cur, to_snapshot_id: int, from_snapshot_id: int | None = None) -> dict:
    """Record the movement created by accepting `to_snapshot_id`.

    Compared against the previous snapshot for Latest movement, and against
    `original_forecast` for the frozen baseline. Only months after the reporting
    cut-off are considered: a completed month keeps its actuals and its original
    baseline, and is never rewritten by a later pending file.
    """
    if from_snapshot_id is None:
        from_snapshot_id = previous_snapshot_for(cur, to_snapshot_id)

    # The first snapshot establishes the baseline; it does not move it. Without
    # this guard every policy in it reads as 'added_after_original', which turns
    # the opening position into a fictitious gain of the entire book.
    if from_snapshot_id is None:
        return {"from_snapshot_id": None, "to_snapshot_id": to_snapshot_id,
                "months": 0, "movements": 0, "by_type": {},
                "note": "first snapshot: baseline established, no movement to record"}

    cut_off = _cut_off_month(cur)

    # Scope is limited to months the NEW snapshot is confirmed to cover.
    #
    # A month missing from a narrower export, a filtered report or an
    # out-of-order upload is "not reported", not "every policy lapsed".
    # Treating absence as removal would let one bad file wipe an otherwise
    # valid Latest Forecast, so a month is only compared once the upload has
    # been confirmed to cover it.
    cur.execute("""
        SELECT forecast_month FROM snapshot_month_coverage
        WHERE snapshot_id = %s AND is_confirmed_complete AND forecast_month > %s
        ORDER BY forecast_month
    """, (to_snapshot_id, cut_off))
    months = [r[0] for r in cur.fetchall()]
    if not months:
        return {"months": 0, "movements": 0}

    cur.execute("DELETE FROM forecast_movement WHERE to_snapshot_id = %s", (to_snapshot_id,))

    detail_expr = ", ".join(f"p.{f}" for f in DETAIL_FIELDS)
    cur.execute(f"""
        WITH new_rows AS (
            SELECT p.policy_id, p.forecast_month, p.source_manager,
                   p.forecast_contribution, {detail_expr}
            FROM forecast_policy p
            WHERE p.snapshot_id = %(to_id)s AND NOT p.is_excluded
              AND p.forecast_month = ANY(%(months)s)
        ),
        old_rows AS (
            SELECT p.policy_id, p.forecast_month, p.source_manager,
                   p.forecast_contribution, {detail_expr}
            FROM forecast_policy p
            WHERE p.snapshot_id = %(from_id)s AND NOT p.is_excluded
              AND p.forecast_month = ANY(%(months)s)
        ),
        orig AS (
            SELECT policy_id, forecast_month, forecast_contribution, source_manager
            FROM original_forecast WHERE grain = 'policy'
        ),
        joined AS (
            SELECT
              COALESCE(n.policy_id, o.policy_id)           AS policy_id,
              COALESCE(n.forecast_month, o.forecast_month) AS forecast_month,
              n.policy_id IS NOT NULL                      AS in_new,
              o.policy_id IS NOT NULL                      AS in_old,
              COALESCE(n.forecast_contribution, 0)         AS latest_income,
              COALESCE(o.forecast_contribution, 0)         AS previous_income,
              n.source_manager                             AS to_manager,
              o.source_manager                             AS from_manager,
              (n.class_abbrev        IS DISTINCT FROM o.class_abbrev
               OR n.underwriter_abbrev IS DISTINCT FROM o.underwriter_abbrev
               OR n.policy_number    IS DISTINCT FROM o.policy_number
               OR n.client_code      IS DISTINCT FROM o.client_code
               OR n.expiry_date      IS DISTINCT FROM o.expiry_date) AS detail_changed
            FROM new_rows n
            FULL OUTER JOIN old_rows o
              ON o.policy_id = n.policy_id AND o.forecast_month = n.forecast_month
        )
        INSERT INTO forecast_movement
          (from_snapshot_id, to_snapshot_id, policy_id, forecast_month, movement_type,
           original_income, previous_income, latest_income, movement_amount,
           from_manager, to_manager, detail_changes,
           added, removed, amount_changed, manager_changed, detail_changed,
           secondary_changes)
        SELECT
          %(from_id)s, %(to_id)s, j.policy_id, j.forecast_month,
          -- Primary classification, for display. The boolean flags below are
          -- what counting should use: a policy can change manager AND amount in
          -- the same snapshot, and movement_type alone would report only one.
          CASE
            WHEN NOT j.in_new THEN 'removed_from_latest'
            WHEN NOT j.in_old THEN 'added_after_original'
            WHEN j.latest_income <> j.previous_income THEN 'amount_changed'
            WHEN j.to_manager IS DISTINCT FROM j.from_manager THEN 'manager_changed'
            WHEN j.detail_changed THEN 'detail_changed'
            ELSE 'unchanged'
          END,
          COALESCE(og.forecast_contribution, 0),
          j.previous_income,
          -- A removed policy contributes zero to Latest. It never becomes a
          -- negative forecast row.
          CASE WHEN j.in_new THEN j.latest_income ELSE 0 END,
          CASE WHEN j.in_new THEN j.latest_income ELSE 0 END - j.previous_income,
          j.from_manager, j.to_manager,
          CASE WHEN j.detail_changed THEN
            jsonb_build_object('detail_changed', true) END,
          NOT j.in_old,
          NOT j.in_new,
          j.in_new AND j.in_old AND j.latest_income <> j.previous_income,
          j.in_new AND j.in_old AND j.to_manager IS DISTINCT FROM j.from_manager,
          j.in_new AND j.in_old AND j.detail_changed,
          ARRAY_REMOVE(ARRAY[
            CASE WHEN j.in_new AND j.in_old
                  AND j.latest_income <> j.previous_income THEN 'amount_changed' END,
            CASE WHEN j.in_new AND j.in_old
                  AND j.to_manager IS DISTINCT FROM j.from_manager
                 THEN 'manager_changed' END,
            CASE WHEN j.in_new AND j.in_old AND j.detail_changed
                 THEN 'detail_changed' END
          ], NULL)::varchar[]
        FROM joined j
        LEFT JOIN orig og
          ON og.policy_id = j.policy_id AND og.forecast_month = j.forecast_month
    """, {"to_id": to_snapshot_id, "from_id": from_snapshot_id,
           "cut_off": cut_off, "months": months})
    inserted = cur.rowcount

    # Latest coverage points at the newest snapshot for every month it covers.
    cur.execute("""
        UPDATE forecast_month_coverage c SET latest_snapshot_id = %s
        WHERE c.forecast_month = ANY(%s)
    """, (to_snapshot_id, months))

    cur.execute("""
        SELECT movement_type, count(*), COALESCE(SUM(movement_amount), 0)
        FROM forecast_movement WHERE to_snapshot_id = %s GROUP BY 1 ORDER BY 1
    """, (to_snapshot_id,))
    by_type = {t: {"policies": n, "amount": amt} for t, n, amt in cur.fetchall()}

    return {"from_snapshot_id": from_snapshot_id, "to_snapshot_id": to_snapshot_id,
            "months": len(months), "movements": inserted, "by_type": by_type}


def movement_summary(cur, snapshot_id: int | None = None) -> list[dict]:
    """Original to Latest reconciliation per month, for reporting view C."""
    cur.execute("""
        SELECT forecast_month,
               SUM(original_income)                                       AS original,
               SUM(latest_income)                                         AS latest,
               COUNT(*) FILTER (WHERE movement_type='removed_from_latest') AS removed_policies,
               COALESCE(SUM(previous_income) FILTER
                        (WHERE movement_type='removed_from_latest'), 0)    AS removed_income,
               COUNT(*) FILTER (WHERE movement_type='added_after_original') AS added_policies,
               COALESCE(SUM(latest_income) FILTER
                        (WHERE movement_type='added_after_original'), 0)   AS added_income,
               COALESCE(SUM(movement_amount) FILTER
                        (WHERE movement_type='amount_changed'), 0)         AS amount_change,
               -- Counted by flag: a transfer that also changed amount is
               -- still a transfer.
               COUNT(*) FILTER (WHERE manager_changed)                     AS manager_transfers,
               COUNT(*) FILTER (WHERE detail_changed)                      AS detail_changes
        FROM forecast_movement
        WHERE (%s::bigint IS NULL OR to_snapshot_id = %s)
        GROUP BY forecast_month ORDER BY forecast_month
    """, (snapshot_id, snapshot_id))
    cols = ("forecast_month", "original", "latest", "removed_policies", "removed_income",
            "added_policies", "added_income", "amount_change", "manager_transfers",
            "detail_changes")
    return [dict(zip(cols, r)) for r in cur.fetchall()]
