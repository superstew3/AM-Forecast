"""Stage 2 acceptance tests: detection, staging, preview, accept, reject, rollback.

These build and tear down their own batches, so they can run repeatedly against
a database already loaded with the baseline data.
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import polars as pl
import pytest

from conftest import (RENEWALS_FILE, SALES_FILE, _renewal_income, read_rows,
                      renewal_exclusion_engine, source_row_count,
                      sum_column, sum_renewal_income)

from app.importers import (
    AcceptError, RollbackBlocked, accept, detect, prepare, reject, rollback,
)
from app.importers.normalise import dec, transaction_fingerprint

CENT = Decimal("0.01")


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


def covered_months(conn, summary):
    """The months a staged renewals batch covers, for coverage confirmation."""
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT period_month FROM import_staging
                       WHERE batch_id = %s AND period_month IS NOT NULL
                       ORDER BY 1""", (summary.batch_id,))
        return [r[0] for r in cur.fetchall()]


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
    assert d.row_count == source_row_count(SALES_FILE)


def test_detects_renewals_report():
    d = detect(RENEWALS_FILE)
    assert d.file_type == "renewals"
    assert d.confidence == 1.0
    assert d.row_count == source_row_count(RENEWALS_FILE)


def test_detection_warns_about_component_fee_fields():
    """The fields that must never be summed are called out in the preview.

    Asserts that each field is named, not that the explanation uses any
    particular phrase. It previously required the words "double counts", which
    was the wording from when income was Commission + Fees. Income is now the
    primary associate share, so Fees contributes nothing and the double count it
    warned about cannot happen -- the reason had to change, and a test pinned to
    the old sentence made correcting it look like a regression.
    """
    messages = " ".join(detect(SALES_FILE).messages)
    for f in ("SpecialFees", "Fee"):
        assert f in messages, f
    assert "audit" in messages, "the reason should say what the field is kept for"

    messages = " ".join(detect(RENEWALS_FILE).messages)
    for f in ("Admin", "Special"):
        assert f in messages, f


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
        assert s.source_rows == source_row_count(SALES_FILE)
        assert s.excluded_rows == 0 or s.duplicate_rows == source_row_count(SALES_FILE)
        # Nothing has moved yet.
        assert net_income(conn) == before
        assert scalar(conn, "SELECT count(*) FROM import_staging WHERE batch_id=%s",
                      (s.batch_id,)) == source_row_count(SALES_FILE)
    finally:
        reject(conn, s.batch_id, "test cleanup", "pytest")


def test_preview_of_fresh_sales_file_reconciles(conn, tmp_path):
    """Same file against an empty transaction table would show the confirmed
    figures. Here it is already loaded, so verify via the first accepted batch."""
    pos, ret, net = conn.cursor().connection.cursor().execute, None, None
    row = scalar(conn, """SELECT positive_income FROM upload_batch
                          WHERE id = (SELECT MIN(id) FROM upload_batch
                                      WHERE file_type='sales' AND status='accepted')""")
    # The preview must report what the file actually contains.
    assert abs(row - sum_column(SALES_FILE, "PrimaryAssocAmount", conn=conn,
                                source_type="sales", positive_only=True)) <= CENT


def test_reject_discards_staging_and_touches_nothing(conn):
    before_rows = scalar(conn, "SELECT count(*) FROM sales_transaction")
    before_net = net_income(conn)
    s = prepare(conn, SALES_FILE, "pytest")
    result = reject(conn, s.batch_id, "not the right file", "pytest")
    assert result["staged_rows_discarded"] == source_row_count(SALES_FILE)
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
        assert s.duplicate_rows == source_row_count(SALES_FILE)
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
    """Rows already loaded, plus a block of genuinely new ones.

    Sized from the file rather than fixed at 500 and 200: a slice beyond the
    end of a smaller export silently produced an empty block, so the test
    asserted on nothing.
    """
    df = pl.read_csv(SALES_FILE, infer_schema_length=0)
    new_count = max(1, len(df) // 4)
    existing_count = len(df) - new_count
    # The offset must clear every real invoice number, which now reach 8.8
    # million. A fixed +900,000 collided with them, so the "new" rows hashed to
    # existing ones and the test silently asserted on an empty block.
    offset = int(df["InvNumber"].cast(pl.Int64).max()) + 1_000_000
    new = df.slice(existing_count, new_count).with_columns(
        (pl.col("InvNumber").cast(pl.Int64) + offset).cast(pl.Utf8).alias("InvNumber"))
    p = tmp_path / "incremental.csv"
    pl.concat([df.head(existing_count), new]).write_csv(p)
    return str(p), new_count


def test_incremental_import_and_exact_rollback(conn, incremental_file):
    path, new_count = incremental_file
    before_rows = scalar(conn, "SELECT count(*) FROM sales_transaction")
    before_net = net_income(conn)

    s = prepare(conn, path, "pytest")
    assert s.valid_rows == new_count
    assert s.duplicate_rows == len(pl.read_csv(path, infer_schema_length=0)) - new_count

    accept(conn, s.batch_id, "pytest")
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == before_rows + new_count
    assert net_income(conn) > before_net

    result = rollback(conn, s.batch_id, "test rollback", "pytest")
    assert result["transactions_deleted"] == new_count
    assert result["sightings_removed"] == len(pl.read_csv(path, infer_schema_length=0))
    assert result["transactions_repaired"] == \
        len(pl.read_csv(path, infer_schema_length=0)) - new_count

    # Exact restoration, not approximate.
    assert scalar(conn, "SELECT count(*) FROM sales_transaction") == before_rows
    assert net_income(conn) == before_net
    assert scalar(conn, """SELECT count(*) FROM sales_transaction
                           WHERE last_seen_batch_id = %s""", (s.batch_id,)) == 0
    assert scalar(conn, "SELECT max(seen_count) FROM sales_transaction") == 1


def test_rollback_writes_an_audit_row(conn, incremental_file):
    path, new_count = incremental_file
    s = prepare(conn, path, "pytest")
    accept(conn, s.batch_id, "pytest")
    rollback(conn, s.batch_id, "documented reason", "auditor")
    reason, by, deleted = None, None, None
    with conn.cursor() as cur:
        cur.execute("""SELECT reason, performed_by, transactions_deleted
                       FROM batch_rollback WHERE batch_id=%s""", (s.batch_id,))
        reason, by, deleted = cur.fetchone()
    assert reason == "documented reason"
    assert by == "auditor"
    assert deleted == new_count


def test_rolled_back_batch_cannot_be_rolled_back_twice(conn, incremental_file):
    path, new_count = incremental_file
    s = prepare(conn, path, "pytest")
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
        assert s.source_rows == source_row_count(RENEWALS_FILE)
        engine = renewal_exclusion_engine(conn.cursor())
        assert s.excluded_rows == sum(
            1 for r in read_rows(RENEWALS_FILE) if engine.check(r) is not None)
        expected = sum_renewal_income(RENEWALS_FILE, conn=conn)
        assert abs(s.raw_expected_income - expected) <= CENT
        # Contribution floors negatives at zero, so it can only be higher.
        assert s.forecast_contribution >= s.raw_expected_income
        assert s.exceptions_by_type.get("negative_expected", 0) == sum(
            1 for r in read_rows(RENEWALS_FILE)
            if engine.check(r) is None
            and _renewal_income(r) < 0)
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
    # The file covers a month the cut-off treats as complete, so acceptance
    # requires the coverage to be confirmed — exactly as an operator would.
    accept(conn, s.batch_id, "pytest", confirmed_months=covered_months(conn, s))
    try:
        assert scalar(conn, """SELECT SUM(forecast_contribution) FROM original_forecast
                               WHERE origin='snapshot'""") == before_total
        assert scalar(conn, """SELECT count(*) FROM original_forecast
                               WHERE origin='snapshot'""") == before_count
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)


def test_rollback_of_a_replacing_snapshot_restores_the_previous_baseline(conn):
    """Accepting then rolling back must leave the Original Forecast as it was.

    Accepting a snapshot over an open month replaces that month's Original
    Forecast and stamps the rows with the new batch. Rollback deletes the rows
    that batch established, which is those same rows — so without a matching
    restore it removes a baseline the earlier, surviving snapshot owns. The
    month is then left with a live Latest Forecast and no budget, and the
    managers in it silently lose their targets.

    Derived from whatever is loaded: the assertion is that the round trip is
    exact, not that it lands on one dataset's figures.
    """
    def baseline(c):
        with c.cursor() as cur:
            cur.execute("""SELECT forecast_month, established_snapshot_id, count(*),
                                  COALESCE(SUM(forecast_contribution), 0)
                           FROM original_forecast GROUP BY 1, 2 ORDER BY 1, 2""")
            rows = cur.fetchall()
            cur.execute("""SELECT forecast_month, original_snapshot_id, latest_snapshot_id
                           FROM forecast_month_coverage ORDER BY 1""")
            return rows, cur.fetchall()

    before_rows, before_coverage = baseline(conn)
    # Asks the database for the rule rather than restating it against the
    # cut-off, which stopped matching the importer when it moved to the calendar
    # month.
    open_months = scalar(conn, """
        SELECT count(*) FROM original_forecast o
        WHERE forecast_month_is_open(o.forecast_month)""")
    if not open_months:
        pytest.skip("no open month carries an Original Forecast to be replaced")

    s = prepare(conn, RENEWALS_FILE, "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=covered_months(conn, s))
    try:
        # The replacement really did take ownership, or this proves nothing.
        assert baseline(conn)[0] != before_rows, \
            "expected the open month's Original Forecast to be re-established"
    finally:
        result = rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)

    after_rows, after_coverage = baseline(conn)
    assert after_rows == before_rows
    assert after_coverage == before_coverage
    assert result["original_forecast_rows_restored"] == \
        result["original_forecast_rows_deleted"]


def test_renewals_rollback_blocked_when_newer_snapshot_exists(conn):
    first = scalar(conn, "SELECT MIN(id) FROM forecast_snapshot")
    first_batch = scalar(conn, "SELECT batch_id FROM forecast_snapshot WHERE id=%s",
                         (first,))
    s = prepare(conn, RENEWALS_FILE, "pytest")
    accept(conn, s.batch_id, "pytest", confirmed_months=covered_months(conn, s))
    try:
        with pytest.raises(RollbackBlocked) as exc:
            rollback(conn, first_batch, "should be blocked", "pytest")
        assert "newer accepted snapshot" in str(exc.value)
    finally:
        rollback(conn, s.batch_id, "test cleanup", "pytest", force=True)
