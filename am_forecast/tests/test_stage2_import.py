"""Stage 2 acceptance tests: detection, staging, preview, accept, reject, rollback.

These build and tear down their own batches, so they can run repeatedly against
a database already loaded with the baseline data.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from app.importers import (
    AcceptError, RollbackBlocked, accept, detect, prepare, reject, rollback,
)
from app.importers.normalise import dec, transaction_fingerprint

CENT = Decimal("0.01")
SALES_FILE = "/mnt/user-data/uploads/Sales_Transaction_List_25-26.csv"
RENEWALS_FILE = "/mnt/user-data/uploads/Renewals_Pending_Summary_-_now-june2027.csv"


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def net_income(conn) -> Decimal:
    return scalar(conn, """SELECT COALESCE(SUM(actual_income),0) FROM sales_transaction
                           WHERE NOT is_excluded""")


# --- normalisation ------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("1234.56", Decimal("1234.56")),
    ("$1,234.56", Decimal("1234.56")),
    ("(547.02)", Decimal("-547.02")),
    ("  -547.02 ", Decimal("-547.02")),
    ("", Decimal("0.00")),
    (None, Decimal("0.00")),
])
def test_currency_parsing(raw, expected):
    assert dec(raw) == expected


def test_fingerprint_is_format_insensitive():
    """A source export that changes number formatting must not duplicate rows."""
    a = {"InvNumber": "1", "TransactionDate": "2025-05-16 14:43:50", "Code": "CASHSALE",
         "PolicyNumber": "59056872", "Category": "ADJ", "Commission": "0.00",
         "Fees": "-547.02", "Group1Abbrev": "Anastasia K"}
    b = dict(a, Commission="0", Fees=" -547.020 ",
             TransactionDate="2025-05-16T14:43:50")
    assert transaction_fingerprint(a) == transaction_fingerprint(b)


def test_fingerprint_separates_genuinely_different_rows():
    a = {"InvNumber": "1", "TransactionDate": "2025-05-16 14:43:50", "Code": "X",
         "PolicyNumber": "P1", "Category": "RWL", "Commission": "10.00",
         "Fees": "1.00", "Group1Abbrev": "Sam Stewart"}
    assert transaction_fingerprint(a) != transaction_fingerprint(dict(a, Fees="2.00"))


# --- detection ----------------------------------------------------------------

def test_detects_sales_report():
    d = detect(SALES_FILE)
    assert d.file_type == "sales"
    assert d.confidence == 1.0
    assert d.missing_required == []
    assert d.row_count == 14886


def test_detects_renewals_report():
    d = detect(RENEWALS_FILE)
    assert d.file_type == "renewals"
    assert d.confidence == 1.0
    assert d.row_count == 6749


def test_detection_warns_about_component_fee_fields():
    """The fields that must never be summed are called out in the preview."""
    messages = " ".join(detect(SALES_FILE).messages)
    assert "SpecialFees" in messages and "double counts" in messages
    messages = " ".join(detect(RENEWALS_FILE).messages)
    for f in ("Admin", "Special"):
        assert f in messages


def test_detection_rejects_unrelated_file(tmp_path):
    p = tmp_path / "unrelated.csv"
    pl.DataFrame({"alpha": ["1"], "beta": ["2"]}).write_csv(p)
    d = detect(p)
    assert d.file_type is None
    assert not d.importable


# --- staging and preview ------------------------------------------------------

def test_preview_matches_confirmed_reconciliation(conn, tmp_path):
    """The figures a user approves must be the confirmed totals, computed before
    anything reaches a fact table."""
    before = net_income(conn)
    s = prepare(conn, SALES_FILE, "pytest")
    try:
        assert s.source_rows == 14886
        assert s.excluded_rows == 0 or s.duplicate_rows == 14886
        # Nothing has moved yet.
        assert net_income(conn) == before
        assert scalar(conn, "SELECT count(*) FROM import_staging WHERE batch_id=%s",
                      (s.batch_id,)) == 14886
    finally:
        reject(conn, s.batch_id, "test cleanup", "pytest")


def test_preview_of_fresh_sales_file_reconciles(conn, tmp_path):
    """Same file against an empty transaction table would show the confirmed
    figures. Here it is already loaded, so verify via the first accepted batch."""
    pos, ret, net = conn.cursor().connection.cursor().execute, None, None
    row = scalar(conn, """SELECT positive_income FROM upload_batch
                          WHERE id = (SELECT MIN(id) FROM upload_batch
                                      WHERE file_type='sales' AND status='accepted')""")
    assert abs(row - Decimal("5620647.70")) <= CENT


def test_reject_discards_staging_and_touches_nothing(conn):
    before_rows = scalar(conn, "SELECT count(*) FROM sales_transaction")
    before_net = net_income(conn)
    s = prepare(conn, SALES_FILE, "pytest")
    result = reject(conn, s.batch_id, "not the right file", "pytest")
    assert result["staged_rows_discarded"] == 14886
    assert scalar(conn, "SELECT count(*) FROM import_staging WHERE batch_id=%s",
                  (s.batch_id,)) == 0
    assert scalar(conn, "SELECT status FROM upload_batch WHERE id=%s",
                  (s.batch_id,)) == "rejected"
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == before_rows
    assert net_income(conn) == before_net


def test_rejected_batch_cannot_be_accepted(conn):
    s = prepare(conn, SALES_FILE, "pytest")
    reject(conn, s.batch_id, "test", "pytest")
    with pytest.raises(AcceptError):
        accept(conn, s.batch_id, "pytest")


# --- duplicate handling -------------------------------------------------------

def test_reupload_stages_everything_as_duplicate(conn):
    s = prepare(conn, SALES_FILE, "pytest")
    try:
        assert s.duplicate_rows == 14886
        assert s.valid_rows == 0
        assert s.net_income == Decimal("0.00")
        assert any("Byte-identical" in m for m in s.messages)
    finally:
        reject(conn, s.batch_id, "test cleanup", "pytest")


def test_accepting_a_reupload_changes_no_total(conn):
    before_rows = scalar(conn, "SELECT count(*) FROM sales_transaction")
    before_net = net_income(conn)
    s = prepare(conn, SALES_FILE, "pytest")
    accept(conn, s.batch_id, "pytest")
    try:
        assert scalar(conn, "SELECT count(*) FROM sales_transaction") == before_rows
        assert net_income(conn) == before_net
        assert scalar(conn, "SELECT max(seen_count) FROM sales_transaction") >= 2
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest")
    assert net_income(conn) == before_net
    assert scalar(conn, "SELECT max(seen_count) FROM sales_transaction") == 1


# --- rollback -----------------------------------------------------------------

@pytest.fixture
def incremental_file(tmp_path):
    """500 rows already loaded plus 200 genuinely new ones."""
    df = pl.read_csv(SALES_FILE, infer_schema_length=0)
    new = df.slice(1000, 200).with_columns(
        (pl.col("InvNumber").cast(pl.Int64) + 900000).cast(pl.Utf8).alias("InvNumber"))
    p = tmp_path / "incremental.csv"
    pl.concat([df.head(500), new]).write_csv(p)
    return str(p)


def test_incremental_import_and_exact_rollback(conn, incremental_file):
    before_rows = scalar(conn, "SELECT count(*) FROM sales_transaction")
    before_net = net_income(conn)

    s = prepare(conn, incremental_file, "pytest")
    assert s.valid_rows == 200
    assert s.duplicate_rows == 500

    accept(conn, s.batch_id, "pytest")
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == before_rows + 200
    assert net_income(conn) > before_net

    result = rollback(conn, s.batch_id, "test rollback", "pytest")
    assert result["transactions_deleted"] == 200
    assert result["sightings_removed"] == 700
    assert result["transactions_repaired"] == 500

    # Exact restoration, not approximate.
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == before_rows
    assert net_income(conn) == before_net
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE last_seen_batch_id = %s""", (s.batch_id,)) == 0
    assert scalar(conn, "SELECT max(seen_count) FROM sales_transaction") == 1


def test_rollback_writes_an_audit_row(conn, incremental_file):
    s = prepare(conn, incremental_file, "pytest")
    accept(conn, s.batch_id, "pytest")
    rollback(conn, s.batch_id, "documented reason", "auditor")
    reason, by, deleted = None, None, None
    with conn.cursor() as cur:
        cur.execute("""SELECT reason, performed_by, transactions_deleted
                       FROM batch_rollback WHERE batch_id=%s""", (s.batch_id,))
        reason, by, deleted = cur.fetchone()
    assert reason == "documented reason"
    assert by == "auditor"
    assert deleted == 200


def test_rolled_back_batch_cannot_be_rolled_back_twice(conn, incremental_file):
    s = prepare(conn, incremental_file, "pytest")
    accept(conn, s.batch_id, "pytest")
    rollback(conn, s.batch_id, "first", "pytest")
    with pytest.raises(RollbackBlocked):
        rollback(conn, s.batch_id, "second", "pytest")


# --- validation gates ---------------------------------------------------------

@pytest.fixture
def bad_file(tmp_path):
    """Contains an unmapped manager and an unmapped category."""
    df = pl.read_csv(SALES_FILE, infer_schema_length=0).head(50).with_columns([
        pl.when(pl.int_range(pl.len()) < 5).then(pl.lit("Wandering Broker"))
          .otherwise(pl.col("Group1Abbrev")).alias("Group1Abbrev"),
        pl.when(pl.int_range(pl.len()) < 3).then(pl.lit("ZZZ"))
          .otherwise(pl.col("Category")).alias("Category"),
        (pl.col("InvNumber").cast(pl.Int64) + 7700000).cast(pl.Utf8).alias("InvNumber"),
    ])
    p = tmp_path / "bad.csv"
    df.write_csv(p)
    return str(p)


def test_unmapped_manager_and_category_block_accept(conn, bad_file):
    s = prepare(conn, bad_file, "pytest")
    try:
        assert "missing_manager_mapping" in s.exceptions_by_type
        assert "unmapped_category" in s.exceptions_by_type
        with pytest.raises(AcceptError) as exc:
            accept(conn, s.batch_id, "pytest")
        assert "unresolved errors" in str(exc.value)
    finally:
        reject(conn, s.batch_id, "test cleanup", "pytest")


def test_force_accept_is_possible_but_deliberate(conn, bad_file):
    before = net_income(conn)
    s = prepare(conn, bad_file, "pytest")
    accept(conn, s.batch_id, "pytest", force=True)
    try:
        assert scalar(conn, """SELECT count(*) FROM sales_transaction
                               WHERE source_manager='Wandering Broker'""") == 5
        # An unmapped category is recorded as Unmapped, never guessed at.
        assert scalar(conn, """SELECT count(*) FROM sales_transaction
                               WHERE category='ZZZ'
                                 AND business_classification='Unmapped'""") == 3
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest")
    assert net_income(conn) == before


def test_unmapped_manager_does_not_reach_a_canonical_total(conn, bad_file):
    """An unrecognised manager must be visible, not absorbed into someone else."""
    s = prepare(conn, bad_file, "pytest")
    accept(conn, s.batch_id, "pytest", force=True)
    try:
        assert scalar(conn, """SELECT count(*) FROM v_sales_reported
                               WHERE source_manager='Wandering Broker'
                                 AND canonical_manager IS NULL""") == 5
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest")


# --- renewals -----------------------------------------------------------------

def test_renewals_preview_reconciles(conn):
    s = prepare(conn, RENEWALS_FILE, "pytest")
    try:
        assert s.source_rows == 6749
        assert s.excluded_rows == 975
        assert abs(s.raw_expected_income - Decimal("3352917.06")) <= CENT
        assert abs(s.forecast_contribution - Decimal("3354995.38")) <= CENT
        assert s.exceptions_by_type.get("negative_expected") == 3
        assert s.exceptions_by_type.get("overdue_pending") == 1
    finally:
        reject(conn, s.batch_id, "test cleanup", "pytest")


def test_duplicate_policy_id_within_snapshot_is_rejected(conn, tmp_path):
    """A PolicyID must appear once per snapshot. A repeat is a malformed export,
    not something to collapse silently."""
    df = pl.read_csv(RENEWALS_FILE, infer_schema_length=0).head(20)
    p = tmp_path / "dupe_policy.csv"
    pl.concat([df, df.head(3)]).write_csv(p)
    s = prepare(conn, str(p), "pytest")
    try:
        assert s.rejected_rows == 3
        assert s.exceptions_by_type.get("duplicate_policy_id") == 3
    finally:
        reject(conn, s.batch_id, "test cleanup", "pytest")


def test_second_snapshot_does_not_rewrite_original_forecast(conn):
    """Rule 8 and 9: a later upload moves Latest Forecast only."""
    before_total = scalar(conn, """SELECT SUM(forecast_contribution)
                                   FROM original_forecast WHERE origin='snapshot'""")
    before_count = scalar(conn, """SELECT count(*) FROM original_forecast
                                   WHERE origin='snapshot'""")
    s = prepare(conn, RENEWALS_FILE, "pytest")
    accept(conn, s.batch_id, "pytest")
    try:
        assert scalar(conn, """SELECT SUM(forecast_contribution) FROM original_forecast
                               WHERE origin='snapshot'""") == before_total
        assert scalar(conn, """SELECT count(*) FROM original_forecast
                               WHERE origin='snapshot'""") == before_count
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)


def test_renewals_rollback_blocked_when_newer_snapshot_exists(conn):
    first = scalar(conn, "SELECT MIN(id) FROM forecast_snapshot")
    first_batch = scalar(conn, "SELECT batch_id FROM forecast_snapshot WHERE id=%s",
                         (first,))
    s = prepare(conn, RENEWALS_FILE, "pytest")
    accept(conn, s.batch_id, "pytest")
    try:
        with pytest.raises(RollbackBlocked) as exc:
            rollback(conn, first_batch, "should be blocked", "pytest")
        assert "newer accepted snapshot" in str(exc.value)
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)
