"""Accept, reject and rollback.

Accept promotes staged rows verbatim. It re-derives nothing, so the figures a
user approved in the preview are the figures that land.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import json as _json

from psycopg2.extras import Json, execute_batch


def _jsonb(obj):
    """JSONB wrapper that tolerates Decimal and date values in audit payloads."""
    return Json(obj, dumps=lambda o: _json.dumps(o, default=str))

from .normalise import ZERO


class AcceptError(Exception):
    pass


class RollbackBlocked(Exception):
    pass


def _batch(cur, batch_id: int):
    cur.execute("""SELECT id, file_type, status, file_name FROM upload_batch WHERE id=%s""",
                (batch_id,))
    row = cur.fetchone()
    if row is None:
        raise AcceptError(f"batch {batch_id} not found")
    return row


def _blocking_exceptions(cur, batch_id: int) -> list[tuple[str, int]]:
    cur.execute("""
        SELECT exception_type, count(*) FROM ingest_exception
        WHERE batch_id = %s AND severity = 'error' AND resolved_at IS NULL
        GROUP BY 1 ORDER BY 1
    """, (batch_id,))
    return cur.fetchall()


# --- accept ------------------------------------------------------------------

def accept(conn, batch_id: int, accepted_by: str, force: bool = False,
           as_of: dt.date | None = None,
           confirmed_months: list[dt.date] | None = None) -> dict:
    """Promote a staged batch into the fact tables.

    `confirmed_months` declares which forecast months this file covers in full.
    Only those are compared for removals. A batch flagged as requiring
    confirmation cannot be accepted without them.
    """
    with conn.cursor() as cur:
        _, file_type, status, file_name = _batch(cur, batch_id)
        if status != "pending":
            raise AcceptError(f"batch {batch_id} is '{status}', not pending")

        cur.execute("""SELECT requires_confirmation, coverage_warnings
                       FROM upload_batch WHERE id=%s""", (batch_id,))
        needs_confirmation, warnings = cur.fetchone()
        if needs_confirmation and not confirmed_months and not force:
            detail = " ".join(warnings) if warnings else ""
            raise AcceptError(
                f"batch {batch_id} needs coverage confirmation before accept. {detail} "
                "Pass confirmed_months=[...] naming the months this file covers in "
                "full, or force=True to accept without comparing removals.")

        blocking = _blocking_exceptions(cur, batch_id)
        if blocking and not force:
            detail = ", ".join(f"{t} x{n}" for t, n in blocking)
            raise AcceptError(
                f"batch {batch_id} has unresolved errors: {detail}. "
                "Resolve them, or accept with force=True to proceed deliberately.")

        if file_type == "sales":
            result = _accept_sales(cur, batch_id)
        elif file_type == "renewals":
            result = _accept_renewals(cur, batch_id, as_of, confirmed_months, accepted_by)
        else:
            result = _accept_legacy(cur, batch_id)

        cur.execute("""UPDATE upload_batch SET status='accepted', accepted_by=%s,
                       accepted_at=now() WHERE id=%s""", (accepted_by, batch_id))
        # Staging is cleared on accept. The batch, its exceptions and, for sales,
        # its sightings are the durable record.
        cur.execute("DELETE FROM import_staging WHERE batch_id=%s", (batch_id,))
    conn.commit()
    result["batch_id"] = batch_id
    result["file_name"] = file_name
    return result


_TXN_COLUMNS = (
    "fingerprint", "transaction_date", "period_month", "financial_year",
    "financial_quarter", "source_manager", "group1_id", "group2_description",
    "client_id", "client_code", "client_code_norm", "policy_number",
    "policy_number_norm", "invoice_number", "username", "category",
    "business_classification", "derived_classification", "policy_class", "uw_code",
    "reason", "premium", "nett", "commission", "fees", "sub_comm",
    "financial_direction", "primary_assoc_code", "primary_assoc_amount",
    "secondary_assoc_code", "secondary_assoc_amount", "is_excluded",
    "exclusion_rule_id", "exclusion_field", "exclusion_value",
)


def _accept_sales(cur, batch_id: int) -> dict:
    cur.execute("""
        SELECT source_row_number, status, prepared, source_row, changed_fields,
               existing_transaction_id
        FROM import_staging
        WHERE batch_id=%s AND status IN ('valid','excluded','duplicate','restated')
        ORDER BY source_row_number
    """, (batch_id,))
    staged = cur.fetchall()

    inserts, sightings, restatements = [], [], []
    for row_no, status, prepared, source_row, changed, existing_id in staged:
        if status in ("valid", "excluded"):
            inserts.append(tuple(prepared[c] for c in _TXN_COLUMNS)
                           + (batch_id, batch_id, Json(source_row), row_no))
        else:
            sightings.append((prepared["fingerprint"], batch_id, row_no))
            if status == "restated":
                restatements.append((prepared["fingerprint"], batch_id, Json(changed)))

    placeholders = ",".join(["%s"] * (len(_TXN_COLUMNS) + 3))
    execute_batch(cur, f"""
        INSERT INTO sales_transaction ({', '.join(_TXN_COLUMNS)},
            first_seen_batch_id, last_seen_batch_id, source_row)
        VALUES ({placeholders})
        ON CONFLICT (fingerprint) DO NOTHING
    """, [i[:-1] for i in inserts], page_size=1000)

    # Sightings for the newly inserted rows.
    execute_batch(cur, """
        INSERT INTO transaction_sighting (transaction_id, batch_id, source_row_number)
        SELECT t.id, %s, %s FROM sales_transaction t WHERE t.fingerprint = %s
        ON CONFLICT (transaction_id, batch_id) DO NOTHING
    """, [(batch_id, i[-1], i[0]) for i in inserts], page_size=1000)

    # Repeat sightings: bump last_seen and seen_count, insert nothing.
    execute_batch(cur, """
        INSERT INTO transaction_sighting (transaction_id, batch_id, source_row_number)
        SELECT t.id, %s, %s FROM sales_transaction t WHERE t.fingerprint = %s
        ON CONFLICT (transaction_id, batch_id) DO NOTHING
    """, [(batch_id, rn, fp) for fp, _, rn in sightings], page_size=1000)

    execute_batch(cur, """
        UPDATE sales_transaction
        SET last_seen_batch_id=%s, last_seen_at=now(), seen_count=seen_count+1
        WHERE fingerprint=%s
    """, [(batch_id, fp) for fp, _, _ in sightings], page_size=1000)

    execute_batch(cur, """
        INSERT INTO restated_transaction (transaction_id, batch_id, changed_fields)
        SELECT t.id, %s, %s FROM sales_transaction t WHERE t.fingerprint=%s
    """, [(batch_id, ch, fp) for fp, _, ch in restatements], page_size=500)

    cur.execute("""
        SELECT COALESCE(SUM(actual_income),0) FROM sales_transaction
        WHERE NOT is_excluded AND id IN (
            SELECT transaction_id FROM transaction_sighting WHERE batch_id=%s)
    """, (batch_id,))
    return {"inserted": len(inserts), "repeat_sightings": len(sightings),
            "restated": len(restatements), "net_income_in_batch": cur.fetchone()[0]}


_POLICY_COLUMNS = (
    "policy_id", "client_id", "client_code", "client_code_norm", "policy_number",
    "policy_number_norm", "class_abbrev", "class_code", "class_description",
    "underwriter_abbrev", "inception_date", "expiry_date", "next_expiry_date",
    "renewal_months", "forecast_month", "financial_year", "financial_quarter",
    "source_manager", "comm", "comm_tax", "fee", "fee_tax", "premium",
    "total_premium",
    # The associate columns drive expected income; comm and fee above are
    # retained as the gross figure for audit only.
    "primary_assoc_comm_sum", "primary_assoc_comm_tax_sum", "primary_assoc_abbrev",
    "exception_flags", "is_excluded", "exclusion_rule_id",
    "exclusion_field", "exclusion_value",
)


def _accept_renewals(cur, batch_id: int, as_of: dt.date | None,
                     confirmed_months: list[dt.date] | None = None,
                     accepted_by: str = "import:accept") -> dict:
    cur.execute("SELECT cut_off_date FROM reporting_settings WHERE id=1")
    cut_off = cur.fetchone()[0]

    # When the extract was taken, inferred from the file rather than defaulting
    # to the cut-off.
    #
    # A Renewals Pending report lists policies that have not yet renewed, so its
    # earliest expiry date is close to the day it was run: an extract taken in
    # August cannot still be carrying April renewals. Defaulting every snapshot
    # to the cut-off made them all look equally recent, which meant an older
    # extract could silently replace a fuller forecast for a month still ahead.
    if as_of is None:
        cur.execute("""SELECT MIN(period_month) FROM import_staging
                       WHERE batch_id = %s AND period_month IS NOT NULL""",
                    (batch_id,))
        earliest = cur.fetchone()[0]
        as_of = earliest or cut_off

    cur.execute("""
        SELECT COUNT(*), COUNT(*) FILTER (WHERE is_excluded),
               COUNT(*) FILTER (WHERE 'negative_expected' = ANY(exception_flags)),
               COUNT(*) FILTER (WHERE 'zero_expected' = ANY(exception_flags)),
               COUNT(*) FILTER (WHERE 'overdue_pending' = ANY(exception_flags)),
               COALESCE(SUM(expected_income),0), COALESCE(SUM(forecast_contribution),0),
               MIN(period_month), MAX(period_month)
        FROM import_staging WHERE batch_id=%s AND status IN ('valid','excluded')
    """, (batch_id,))
    (total, excluded, neg, zero, overdue, raw, contrib, cov_start, cov_end) = cur.fetchone()

    cur.execute("""
        INSERT INTO forecast_snapshot
          (batch_id, as_of_date, coverage_start, coverage_end, source_row_count,
           included_row_count, excluded_row_count, negative_row_count, zero_row_count,
           overdue_row_count, raw_expected_income, forecast_contribution)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
    """, (batch_id, as_of, cov_start, cov_end, total, total - excluded, excluded,
          neg, zero, overdue, raw, contrib))
    snapshot_id = cur.fetchone()[0]

    cur.execute("""
        SELECT prepared, source_row FROM import_staging
        WHERE batch_id=%s AND status IN ('valid','excluded') ORDER BY source_row_number
    """, (batch_id,))
    rows = [(snapshot_id,) + tuple(p[c] for c in _POLICY_COLUMNS) + (Json(src),)
            for p, src in cur.fetchall()]
    placeholders = ",".join(["%s"] * (len(_POLICY_COLUMNS) + 2))
    execute_batch(cur, f"""
        INSERT INTO forecast_policy (snapshot_id, {', '.join(_POLICY_COLUMNS)}, source_row)
        VALUES ({placeholders})
    """, rows, page_size=1000)

    # Original Forecast: established only for months that have none, and only
    # for months after the reporting cut-off. A completed month keeps its
    # baseline; later uploads move Latest Forecast alone.
    # Open months this snapshot covers are cleared first, so a newer file
    # replaces the forecast for a month still ahead rather than adding a second
    # set of rows to it.
    #
    # Only where the snapshot is genuinely newer. "Later information is better
    # information" holds only if it is later: loading an older extract must not
    # overwrite a month with the thinner view that extract had of it. An April
    # file carries two August policies because August was months away; letting
    # it replace a full August forecast would destroy the figure while looking
    # like a routine update.
    #
    # Closed and pinned months are excluded by the same conditions used below,
    # so nothing already measured against can be removed here.
    cur.execute("""
        DELETE FROM original_forecast o
        WHERE forecast_month_writable(o.forecast_month)
          AND o.forecast_month IN (
              SELECT DISTINCT forecast_month FROM forecast_policy
              WHERE snapshot_id = %s AND NOT is_excluded)
          -- Newer than whatever established the month.
          AND COALESCE((SELECT MAX(s.as_of_date) FROM forecast_snapshot s
                        JOIN forecast_policy fp ON fp.snapshot_id = s.id
                        WHERE fp.forecast_month = o.forecast_month
                          AND s.id <> %s), DATE '1900-01-01')
              <= (SELECT as_of_date FROM forecast_snapshot WHERE id = %s)
    """, (snapshot_id, snapshot_id, snapshot_id))
    replaced = cur.rowcount

    cur.execute("""
        INSERT INTO original_forecast
          (grain, policy_id, forecast_month, financial_year, financial_quarter,
           origin, established_snapshot_id, established_batch_id, established_by,
           source_manager, client_code, policy_number, class_abbrev,
           expected_income, forecast_contribution)
        SELECT 'policy', p.policy_id, p.forecast_month, p.financial_year,
               p.financial_quarter, 'snapshot', p.snapshot_id, %s, 'import:accept',
               p.source_manager, p.client_code, p.policy_number, p.class_abbrev,
               p.raw_expected_income, p.forecast_contribution
        FROM forecast_policy p
        WHERE p.snapshot_id = %s AND NOT p.is_excluded
          -- The month must be writable: after the current calendar month in
          -- Melbourne, not pinned, or covered by an unconsumed admin override.
          --
          -- This replaces a test against the reporting cut-off. The cut-off is a
          -- setting somebody maintains, and while it was wrong -- as it was here
          -- for a week, left at 2025-12-31 by a test run -- every upload wrote
          -- months it had no business touching. The calendar cannot be left
          -- stale by accident.
          AND forecast_month_writable(p.forecast_month)
          -- A month is only written where nothing already holds it, so an older
          -- extract cannot thin out a fuller forecast.
          AND NOT EXISTS (SELECT 1 FROM original_forecast o
                          WHERE o.forecast_month = p.forecast_month)
        ON CONFLICT DO NOTHING
    """, (batch_id, snapshot_id))
    established = cur.rowcount

    cur.execute("""
        INSERT INTO forecast_month_coverage
          (forecast_month, original_snapshot_id, latest_snapshot_id, original_grain)
        SELECT DISTINCT forecast_month, %s, %s, 'policy'
        FROM forecast_policy WHERE snapshot_id=%s AND NOT is_excluded
          AND forecast_month > date_trunc('month', %s::date)
        ON CONFLICT (forecast_month)
        DO UPDATE SET latest_snapshot_id = EXCLUDED.latest_snapshot_id
    """, (snapshot_id, snapshot_id, snapshot_id, cut_off))

    # Coverage must name the snapshot the Original Forecast rows actually carry.
    # Replacing an open month above re-establishes those rows under this
    # snapshot; leaving original_snapshot_id on the superseded one made coverage
    # and rows disagree about who owns the month, and a later rollback then
    # deleted rows it believed it had established while coverage still pointed
    # at a snapshot that no longer had any. Months this snapshot did not write —
    # closed, pinned, or already established — are untouched.
    cur.execute("""
        UPDATE forecast_month_coverage c
        SET original_snapshot_id = o.snapshot_id
        FROM (SELECT forecast_month, MAX(established_snapshot_id) AS snapshot_id
              FROM original_forecast WHERE established_snapshot_id = %s
              GROUP BY forecast_month) o
        WHERE c.forecast_month = o.forecast_month
          AND c.original_snapshot_id IS DISTINCT FROM o.snapshot_id
    """, (snapshot_id,))

    cur.execute("""UPDATE forecast_snapshot SET is_superseded=true
                   WHERE id <> %s AND is_superseded=false""", (snapshot_id,))

    # Declare which months this file covers before any comparison happens.
    # Without a declaration nothing is compared and nothing can be removed.
    from ..forecast.coverage import record_coverage
    record_coverage(cur, snapshot_id, confirmed_months)
    if confirmed_months:
        cur.execute("""UPDATE upload_batch SET confirmed_by=%s, confirmed_at=now(),
                       confirmed_months=%s WHERE id=%s""",
                    (accepted_by, list(confirmed_months), batch_id))

    # Movement against the previous snapshot, restricted to confirmed months.
    from ..forecast.movement import compute_movement
    movement = compute_movement(cur, snapshot_id)

    return {"snapshot_id": snapshot_id, "policies": len(rows),
            "original_forecast_rows_established": established,
            "original_forecast_rows_replaced": replaced,
            "forecast_contribution": contrib,
            "movement": movement}


def _accept_legacy(cur, batch_id: int) -> dict:
    cur.execute("""SELECT prepared FROM import_staging
                   WHERE batch_id=%s AND status='valid' ORDER BY source_row_number""",
                (batch_id,))
    rows = [p for (p,) in cur.fetchall()]
    execute_batch(cur, """
        INSERT INTO legacy_forecast_reference
          (batch_id, forecast_month, financial_year, financial_quarter, source_manager,
           forecast_amount, promoted_to_original, is_verified_exclusion_clean, note)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (forecast_month, source_manager) DO UPDATE SET
          forecast_amount = EXCLUDED.forecast_amount,
          promoted_to_original = EXCLUDED.promoted_to_original
    """, [(batch_id, r["forecast_month"], r["financial_year"], r["financial_quarter"],
           r["source_manager"], r["forecast_amount"], r["promoted_to_original"],
           r["is_verified_exclusion_clean"], r["note"]) for r in rows], page_size=500)

    cur.execute("""
        INSERT INTO original_forecast
          (grain, policy_id, forecast_month, financial_year, financial_quarter,
           origin, established_batch_id, established_by, source_manager,
           expected_income, forecast_contribution, note)
        SELECT 'manager_month', NULL, l.forecast_month, l.financial_year,
               l.financial_quarter, 'legacy_dashboard', %s, 'import:accept',
               l.source_manager, l.forecast_amount, GREATEST(l.forecast_amount, 0),
               'Legacy Dashboard Forecast. Manager-month grain; no policy detail.'
        FROM legacy_forecast_reference l
        WHERE l.batch_id = %s AND l.promoted_to_original
        ON CONFLICT DO NOTHING
    """, (batch_id, batch_id))
    return {"reference_rows": len(rows), "promoted_to_original": cur.rowcount}


# --- reject ------------------------------------------------------------------

def reject(conn, batch_id: int, reason: str, rejected_by: str) -> dict:
    with conn.cursor() as cur:
        _, _, status, file_name = _batch(cur, batch_id)
        if status != "pending":
            raise AcceptError(f"batch {batch_id} is '{status}', not pending")
        cur.execute("SELECT count(*) FROM import_staging WHERE batch_id=%s", (batch_id,))
        discarded = cur.fetchone()[0]
        cur.execute("DELETE FROM import_staging WHERE batch_id=%s", (batch_id,))
        cur.execute("""UPDATE upload_batch
                       SET status='rejected', rollback_reason=%s, rolled_back_by=%s,
                           rolled_back_at=now() WHERE id=%s""",
                    (reason, rejected_by, batch_id))
    conn.commit()
    return {"batch_id": batch_id, "file_name": file_name, "staged_rows_discarded": discarded}


# --- rollback ----------------------------------------------------------------

def rollback(conn, batch_id: int, reason: str, performed_by: str,
             force: bool = False) -> dict:
    """Reverse an accepted batch without disturbing other uploads."""
    with conn.cursor() as cur:
        _, file_type, status, file_name = _batch(cur, batch_id)
        if status != "accepted":
            raise RollbackBlocked(f"batch {batch_id} is '{status}', not accepted")

        if file_type == "sales":
            result = _rollback_sales(cur, batch_id)
        elif file_type == "renewals":
            result = _rollback_renewals(cur, batch_id, force)
        else:
            result = _rollback_legacy(cur, batch_id)

        cur.execute("""
            INSERT INTO batch_rollback
              (batch_id, reason, performed_by, transactions_deleted, sightings_removed,
               snapshots_deleted, original_forecast_rows_deleted, net_income_reversed,
               forecast_reversed, detail)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (batch_id, reason, performed_by,
              result.get("transactions_deleted", 0), result.get("sightings_removed", 0),
              result.get("snapshots_deleted", 0),
              result.get("original_forecast_rows_deleted", 0),
              result.get("net_income_reversed", ZERO),
              result.get("forecast_reversed", ZERO), _jsonb(result)))
        cur.execute("""UPDATE upload_batch SET status='rolled_back', rollback_reason=%s,
                       rolled_back_by=%s, rolled_back_at=now() WHERE id=%s""",
                    (reason, performed_by, batch_id))
    conn.commit()
    result["batch_id"] = batch_id
    result["file_name"] = file_name
    return result


def _rollback_sales(cur, batch_id: int) -> dict:
    # Rows whose only sighting is this batch disappear. Rows also seen in other
    # batches survive, with last_seen restored to the newest remaining batch.
    cur.execute("""
        SELECT COALESCE(SUM(t.actual_income),0)
        FROM sales_transaction t
        WHERE NOT t.is_excluded AND t.id IN (
            SELECT s.transaction_id FROM transaction_sighting s
            WHERE s.batch_id=%s
            GROUP BY s.transaction_id
            HAVING NOT EXISTS (SELECT 1 FROM transaction_sighting o
                               WHERE o.transaction_id = s.transaction_id
                                 AND o.batch_id <> %s))
    """, (batch_id, batch_id))
    reversed_income = cur.fetchone()[0]

    cur.execute("DELETE FROM transaction_sighting WHERE batch_id=%s", (batch_id,))
    sightings_removed = cur.rowcount

    cur.execute("""
        DELETE FROM sales_transaction t
        WHERE NOT EXISTS (SELECT 1 FROM transaction_sighting s
                          WHERE s.transaction_id = t.id)
    """)
    deleted = cur.rowcount

    cur.execute("""
        UPDATE sales_transaction t SET
            last_seen_batch_id = latest.batch_id,
            last_seen_at = latest.seen_at,
            seen_count = latest.n
        FROM (
            SELECT s.transaction_id,
                   (array_agg(s.batch_id ORDER BY s.batch_id DESC))[1] AS batch_id,
                   max(s.seen_at) AS seen_at,
                   count(*)::int AS n
            FROM transaction_sighting s GROUP BY s.transaction_id
        ) latest
        WHERE t.id = latest.transaction_id
          AND (t.last_seen_batch_id = %s OR t.seen_count <> latest.n)
    """, (batch_id,))
    repaired = cur.rowcount

    cur.execute("DELETE FROM restated_transaction WHERE batch_id=%s", (batch_id,))
    return {"transactions_deleted": deleted, "sightings_removed": sightings_removed,
            "transactions_repaired": repaired, "net_income_reversed": reversed_income}


def _rollback_renewals(cur, batch_id: int, force: bool) -> dict:
    cur.execute("SELECT id FROM forecast_snapshot WHERE batch_id=%s", (batch_id,))
    snapshot_ids = [r[0] for r in cur.fetchall()]
    if not snapshot_ids:
        return {"snapshots_deleted": 0}

    cur.execute("""
        SELECT count(*) FROM forecast_snapshot s
        JOIN upload_batch b ON b.id = s.batch_id AND b.status='accepted'
        WHERE s.id > %s
    """, (max(snapshot_ids),))
    newer = cur.fetchone()[0]
    if newer and not force:
        raise RollbackBlocked(
            f"{newer} newer accepted snapshot(s) exist. Rolling this back would leave "
            "the Original Forecast for its months without the snapshot that established "
            "it. Roll back the newer snapshots first, or force and rebaseline "
            "deliberately.")

    cur.execute("""SELECT COALESCE(SUM(forecast_contribution),0) FROM original_forecast
                   WHERE established_batch_id=%s""", (batch_id,))
    forecast_reversed = cur.fetchone()[0]

    cur.execute("DELETE FROM original_forecast WHERE established_batch_id=%s", (batch_id,))
    orig_deleted = cur.rowcount

    # Hand the Original Forecast back to the newest surviving snapshot, exactly
    # as Latest is handed back below. Accepting a snapshot for an open month
    # replaces that month's Original rows and stamps them with the new batch, so
    # the delete above removes a baseline an earlier, still-present snapshot had
    # established. Restoring only Latest left the month with a live forecast and
    # no budget at all: every manager in it silently lost their target, and the
    # reconciliation check reported an unexplained gap. Closed and pinned months
    # never carry this batch's stamp, so they are not reached here; the guards
    # are repeated anyway so a future change to the accept path cannot let a
    # rollback rewrite a month that has already been measured against.
    cur.execute("SELECT cut_off_date FROM reporting_settings WHERE id=1")
    cut_off = cur.fetchone()[0]
    cur.execute("""
        INSERT INTO original_forecast
          (grain, policy_id, forecast_month, financial_year, financial_quarter,
           origin, established_snapshot_id, established_batch_id, established_by,
           source_manager, client_code, policy_number, class_abbrev,
           expected_income, forecast_contribution)
        SELECT 'policy', p.policy_id, p.forecast_month, p.financial_year,
               p.financial_quarter, 'snapshot', p.snapshot_id, s.batch_id,
               'import:rollback', p.source_manager, p.client_code, p.policy_number,
               p.class_abbrev, p.raw_expected_income, p.forecast_contribution
        FROM forecast_policy p
        JOIN forecast_snapshot s ON s.id = p.snapshot_id
        JOIN (
            SELECT fp.forecast_month, MAX(fp.snapshot_id) AS snapshot_id
            FROM forecast_policy fp
            WHERE NOT fp.is_excluded AND fp.snapshot_id <> ALL(%s)
            GROUP BY fp.forecast_month
        ) newest ON newest.forecast_month = p.forecast_month
                AND newest.snapshot_id = p.snapshot_id
        WHERE NOT p.is_excluded
          AND p.forecast_month > date_trunc('month', %s::date)
          AND NOT EXISTS (SELECT 1 FROM forecast_month_lock l
                          WHERE l.forecast_month = p.forecast_month AND l.active)
          AND NOT EXISTS (SELECT 1 FROM original_forecast o
                          WHERE o.forecast_month = p.forecast_month)
        ON CONFLICT DO NOTHING
    """, (snapshot_ids, cut_off))
    orig_restored = cur.rowcount

    # Coverage follows those rows, so the month is not left naming a snapshot
    # that no longer holds its Original Forecast.
    cur.execute("""
        UPDATE forecast_month_coverage c
        SET original_snapshot_id = prev.snapshot_id
        FROM (
            SELECT o.forecast_month, MAX(o.established_snapshot_id) AS snapshot_id
            FROM original_forecast o
            WHERE o.established_snapshot_id IS NOT NULL
              AND o.established_snapshot_id <> ALL(%s)
            GROUP BY o.forecast_month
        ) prev
        WHERE c.forecast_month = prev.forecast_month
          AND c.original_snapshot_id = ANY(%s)
    """, (snapshot_ids, snapshot_ids))

    # Point Latest back at the newest surviving snapshot that covers each month,
    # rather than deleting coverage that belongs to snapshots still in place.
    cur.execute("""
        UPDATE forecast_month_coverage c
        SET latest_snapshot_id = prev.snapshot_id
        FROM (
            SELECT p.forecast_month, MAX(p.snapshot_id) AS snapshot_id
            FROM forecast_policy p
            WHERE NOT p.is_excluded AND p.snapshot_id <> ALL(%s)
            GROUP BY p.forecast_month
        ) prev
        WHERE c.forecast_month = prev.forecast_month
          AND c.latest_snapshot_id = ANY(%s)
    """, (snapshot_ids, snapshot_ids))
    # Only now remove coverage for months that no surviving snapshot covers.
    cur.execute("""
        DELETE FROM forecast_month_coverage c
        WHERE c.latest_snapshot_id = ANY(%s) OR c.original_snapshot_id = ANY(%s)
    """, (snapshot_ids, snapshot_ids))
    cur.execute("""
        UPDATE forecast_month_coverage c
        SET original_snapshot_id = prev.snapshot_id
        FROM (
            SELECT o.forecast_month, MIN(o.established_snapshot_id) AS snapshot_id
            FROM original_forecast o
            WHERE o.established_snapshot_id IS NOT NULL
            GROUP BY o.forecast_month
        ) prev
        WHERE c.forecast_month = prev.forecast_month
          AND c.original_snapshot_id IS NULL
    """)
    cur.execute("DELETE FROM forecast_movement WHERE to_snapshot_id = ANY(%s)",
                (snapshot_ids,))
    cur.execute("DELETE FROM forecast_policy WHERE snapshot_id = ANY(%s)", (snapshot_ids,))
    cur.execute("DELETE FROM forecast_snapshot WHERE id = ANY(%s)", (snapshot_ids,))
    deleted = cur.rowcount

    cur.execute("""UPDATE forecast_snapshot SET is_superseded=false
                   WHERE id = (SELECT max(id) FROM forecast_snapshot)""")
    return {"snapshots_deleted": deleted, "original_forecast_rows_deleted": orig_deleted,
            "original_forecast_rows_restored": orig_restored,
            "forecast_reversed": forecast_reversed}


def _rollback_legacy(cur, batch_id: int) -> dict:
    cur.execute("""SELECT COALESCE(SUM(forecast_contribution),0) FROM original_forecast
                   WHERE established_batch_id=%s""", (batch_id,))
    reversed_amount = cur.fetchone()[0]
    cur.execute("DELETE FROM original_forecast WHERE established_batch_id=%s", (batch_id,))
    orig = cur.rowcount
    cur.execute("DELETE FROM legacy_forecast_reference WHERE batch_id=%s", (batch_id,))
    return {"original_forecast_rows_deleted": orig, "forecast_reversed": reversed_amount,
            "reference_rows_deleted": cur.rowcount}
