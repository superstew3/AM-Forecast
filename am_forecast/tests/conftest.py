import sys
from pathlib import Path

import os

import psycopg2
import pytest

# The suite drives the API with X-User / X-Role headers rather than signing in
# for every one of 200-odd tests. That path exists only when this flag is set,
# and it must never be set in production. tests/test_stage10_auth.py asserts
# that with the flag unset a session is the only way in.
os.environ.setdefault("AM_FORECAST_DEV_AUTH", "1")

# The source files the suite imports from. Defined once here, so pointing the
# tests at a new export is a single change rather than a hunt through every
# file — which is what made the last dataset change so laborious.
#
# Override the directory with AM_FORECAST_FIXTURES; tests needing the files skip
# when they are absent.
FIXTURE_DIR = Path(os.environ.get("AM_FORECAST_FIXTURES", "/mnt/user-data/uploads"))

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _recency_key(path: Path, file_type: str):
    """How new an export is, judged from its contents rather than its name.

    Renewals: the earliest expiry it contains. A pending report cannot hold
    renewals that have already happened, so the later that earliest expiry, the
    later the extract — the same inference the importer uses to stop an older
    file overwriting a newer forecast.

    Sales: the latest transaction it contains.

    Parsed with the application's own date reader, not compared as text. These
    exports do not agree on a date format: one writes 31/08/2026, another
    2026-08-01. Sorting those as strings ranked a April extract above an August
    one, which is precisely the mistake this tiebreak exists to prevent.
    """
    import datetime as dt

    try:
        import polars as pl

        from app.importers.normalise import parse_date

        column = "ExpiryDate" if file_type == "renewals" else "TransactionDate"
        raw = pl.read_csv(str(path), infer_schema_length=0)[column].drop_nulls().to_list()
        parsed = []
        for value in raw:
            try:
                parsed.append(parse_date(value))
            except Exception:
                continue
        if not parsed:
            return dt.date.min
        return min(parsed) if file_type == "renewals" else max(parsed)
    except Exception:
        return dt.date.min


def _discover(file_type: str) -> str:
    """The one file in FIXTURE_DIR that the app detects as this report type.

    These were two hard-coded filenames from one particular export. A deployment
    holding the same two reports under its own names then failed every test that
    opened them — fifty failures, seventeen errors, all of them one filename that
    did not exist. Worse, the failure was FileNotFoundError rather than a skip,
    so it read as fifty broken tests rather than as a missing fixture.

    Detection is the application's own, the same routine the upload screen uses,
    so the suite follows whatever export is actually present rather than what it
    was once called.

    Where a directory holds more than one export of a type, the newest wins.
    Taking whichever sorted first quietly selected a months-old extract sitting
    beside the current one — the same way round as the bug that let an April file
    wipe August's forecast, and just as invisible.
    """
    if not FIXTURE_DIR.is_dir():
        return str(FIXTURE_DIR / f"{file_type}-not-found.csv")

    candidates = sorted(FIXTURE_DIR.glob("*.csv"))
    matched: list[Path] = []
    try:
        from app.importers import detect

        for path in candidates:
            try:
                d = detect(str(path))
            except Exception:
                continue
            if d.file_type == file_type and d.usable:
                matched.append(path)
    except Exception:
        pass

    if not matched:
        word = "sales" if file_type == "sales" else "renewals"
        matched = [p for p in candidates if word in p.name.lower()]
    if not matched:
        return str(FIXTURE_DIR / f"{file_type}-not-found.csv")
    if len(matched) == 1:
        return str(matched[0])
    return str(max(matched, key=lambda p: (_recency_key(p, file_type), p.name)))


SALES_FILE = _discover("sales")
RENEWALS_FILE = _discover("renewals")


def pytest_configure(config):
    """Stop once, clearly, when the source exports are not where the suite looks.

    Missing files previously surfaced as FileNotFoundError inside thirty-odd
    tests and seventeen fixture errors, which reads as a broken application
    rather than as an unset path. One line naming the directory and what is
    missing is worth more than fifty tracebacks that all say the same thing.

    A hard stop rather than a skip: the database under test was built from these
    files, so a run without them is not a narrower run, it is a meaningless one.
    """
    missing = [label for label, path in (("sales transaction", SALES_FILE),
                                         ("renewals pending", RENEWALS_FILE))
               if not Path(path).is_file()]
    if missing:
        raise pytest.UsageError(
            f"Source export not found: {', '.join(missing)}.\n"
            f"Looked in: {FIXTURE_DIR}\n"
            f"Set AM_FORECAST_FIXTURES to the directory holding the sales and "
            f"renewals CSVs, for example:\n"
            f"    AM_FORECAST_FIXTURES=fixtures pytest tests/ --dsn \"$DATABASE_URL\"\n"
            f"The files are matched by content, not by name, so they do not need "
            f"renaming.")


def pytest_addoption(parser):
    parser.addoption("--dsn", action="store", default="dbname=am_forecast")


@pytest.fixture(scope="session")
def conn(request):
    c = psycopg2.connect(request.config.getoption("--dsn"))
    yield c
    c.close()


# Connections opened by module-scoped fixtures, which the shared cleanup below
# would otherwise never see. A failed statement on one of those leaves it in an
# aborted transaction, and every later test on that connection fails with
# "current transaction is aborted" — one root cause producing a page of
# unrelated-looking failures.
_EXTRA_CONNECTIONS: list = []


def register_connection(connection) -> None:
    if connection not in _EXTRA_CONNECTIONS:
        _EXTRA_CONNECTIONS.append(connection)


def unregister_connection(connection) -> None:
    if connection in _EXTRA_CONNECTIONS:
        _EXTRA_CONNECTIONS.remove(connection)


@pytest.fixture(autouse=True)
def _clean_transaction(conn):
    """Reset every connection between tests so one failure cannot cascade.

    Unconditional: a test that raises, skips, or simply forgets cannot leave a
    connection unusable for the tests that follow. Placing this at the fixture
    rather than in each test means no future test can reintroduce the problem.
    """
    yield
    for c in [conn, *_EXTRA_CONNECTIONS]:
        try:
            c.rollback()
        except Exception:
            # A closed connection is not an error worth failing a test over.
            pass


# --- period helpers -----------------------------------------------------------
#
# The suite used to hardcode July 2026 and FY2026-27 throughout, because that is
# what the first dataset held. Every one of those references broke when a new
# export arrived, which said nothing about whether the system was correct. These
# derive the period from the reporting cut-off instead.

def current_period(conn):
    """The reporting month, its financial year and quarter, from the cut-off."""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT date_trunc('month', cut_off_date)::date AS month,
                   au_financial_year(cut_off_date)         AS financial_year,
                   au_quarter(cut_off_date)                AS financial_quarter,
                   cut_off_date
            FROM reporting_settings WHERE id = 1""")
        month, fy, quarter, cut_off = cur.fetchone()
    return {"month": month, "month_iso": month.isoformat(), "financial_year": fy,
            "financial_quarter": quarter, "cut_off_date": cut_off}


@pytest.fixture
def period(conn):
    return current_period(conn)


@pytest.fixture
def closed_month(conn):
    """A month that has definitively closed, and the period around it.

    Several rules only exist for completed periods: a closed month reports
    actuals and has no Latest Forecast, achievement is measurable, and a future
    month shows an em dash rather than N/A. None of that can be tested against a
    dataset whose only month is still open.

    Rather than weaken those assertions to whatever the current sample happens
    to support — which would quietly stop testing the rule — this moves the
    reporting cut-off to the end of the earliest month holding actuals, and puts
    it back afterwards. The rule stays fully tested on any dataset.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT cut_off_date FROM reporting_settings WHERE id=1")
        original = cur.fetchone()[0]
        cur.execute("""SELECT MIN(date_trunc('month', transaction_date))::date
                       FROM sales_transaction WHERE NOT is_excluded""")
        first = cur.fetchone()[0]
        if first is None:
            pytest.skip("no actuals loaded")
        # End of that month.
        cur.execute("SELECT (%s::date + INTERVAL '1 month - 1 day')::date", (first,))
        month_end = cur.fetchone()[0]
        cur.execute("UPDATE reporting_settings SET cut_off_date=%s WHERE id=1",
                    (month_end,))
        cur.execute("""SELECT au_financial_year(%s::date), au_quarter(%s::date)""",
                    (month_end, month_end))
        fy, quarter = cur.fetchone()
    conn.commit()

    yield {"month": first, "month_iso": first.isoformat(), "cut_off": month_end,
           "financial_year": fy, "financial_quarter": quarter}

    with conn.cursor() as cur:
        cur.execute("UPDATE reporting_settings SET cut_off_date=%s WHERE id=1",
                    (original,))
    conn.commit()


def source_row_count(path) -> int:
    """Rows in a source CSV, excluding the header.

    Tests previously hardcoded the count of one export. Reading it from the file
    under test keeps the assertion meaningful — it still catches an importer
    that drops rows — without breaking every time the data changes.
    """
    import csv as _csv
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return sum(1 for _ in _csv.DictReader(fh))


def read_rows(path):
    import csv as _csv
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return list(_csv.DictReader(fh))


def _dec(value):
    from decimal import Decimal
    v = (value or "").strip().replace(",", "").replace("$", "")
    if not v or v in ("-", "NULL"):
        return Decimal(0)
    if v.startswith("(") and v.endswith(")"):
        return -Decimal(v[1:-1])
    try:
        return Decimal(v)
    except Exception:
        return Decimal(0)


def sum_column(path, column, positive_only=False, exclude_highview=True):
    """Total a column straight from the source file.

    Lets a test assert that the importer reported what the file contained,
    which is the property worth checking, rather than a figure that belongs to
    one particular export.
    """
    from decimal import Decimal
    total = Decimal(0)
    for r in read_rows(path):
        if exclude_highview and (r.get("PrimaryAssocCode") or "").strip().upper() == "HIGHVIEW":
            continue
        value = _dec(r.get(column))
        if positive_only:
            value = max(value, Decimal(0))
        total += value
    return total


def is_excluded_renewal(row) -> bool:
    """Whether a renewal row is excluded, mirroring the seeded rules.

    Exclusion is on the account manager and the secondary associate, not the
    primary associate — a distinction that matters, because the primary
    associate is now the income column.
    """
    manager = (row.get("PolicyAccountManager") or "").strip().upper()
    secondary = (row.get("SecondaryAssocAbbrev") or "").strip().upper()
    primary = (row.get("PrimaryAssocAbbrev") or "").strip().upper()
    return ("HIGHVIEW" in manager or "CAMHIGH" in manager
            or "CAMHIGH" in secondary or "HIGHVIEW" in secondary
            or "HIGHVIEW" in primary)


def _renewal_income(row):
    """SIG expected income for one renewal row, GST inclusive."""
    return _dec(row.get("PrimaryAssocCommSum")) + _dec(row.get("PrimaryAssocCommTaxSum"))


def sum_renewal_income(path, exclude_highview=True):
    """SIG expected income: the associate share, GST inclusive."""
    from decimal import Decimal
    total = Decimal(0)
    for r in read_rows(path):
        if exclude_highview and is_excluded_renewal(r):
            continue
        total += _dec(r.get("PrimaryAssocCommSum")) + _dec(r.get("PrimaryAssocCommTaxSum"))
    return total


# Batches a longer-lived fixture is still using. The autouse cleanup runs after
# every test, so without this it rolls back a module-scoped fixture's data
# part-way through the module that depends on it.
_PROTECTED_BATCHES: set[int] = set()


def protect_batch(batch_id: int) -> None:
    _PROTECTED_BATCHES.add(batch_id)


def release_batch(batch_id: int) -> None:
    _PROTECTED_BATCHES.discard(batch_id)


@pytest.fixture(autouse=True)
def _clean_stranded_batches(request):
    """Roll back anything a test left accepted.

    Several tests deliberately drive rollback into a blocked state, which leaves
    their batch accepted. The rows then persist into later tests: transaction
    counts drift upward, seen_count climbs, and failures appear in tests that
    are themselves correct. Cleaning up afterwards keeps each test independent
    of the order it ran in.
    """
    yield
    if "conn" not in request.fixturenames:
        return
    conn = request.getfixturevalue("conn")
    from app.importers import rollback
    while True:
        with conn.cursor() as cur:
            cur.execute("""SELECT id FROM upload_batch
                           WHERE status = 'accepted' AND id > 2
                             AND NOT (id = ANY(%s))
                           ORDER BY id DESC LIMIT 1""",
                        (list(_PROTECTED_BATCHES) or [-1],))
            row = cur.fetchone()
        if row is None:
            break
        try:
            rollback(conn, row[0], "test teardown", "pytest", force=True)
        except Exception:
            # Cannot be rolled back (a newer snapshot depends on it, say).
            # Mark it so the loop terminates rather than retrying forever.
            conn.rollback()
            with conn.cursor() as cur:
                cur.execute("""UPDATE upload_batch SET status = 'rolled_back'
                               WHERE id = %s""", (row[0],))
            conn.commit()


def budget_months(conn):
    """Months that carry a budget, oldest first."""
    with conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT forecast_month FROM v_monthly_budget
                       ORDER BY 1""")
        return [r[0] for r in cur.fetchall()]


def budgeted_manager(conn, rank=0):
    """A manager who actually has a budget.

    Tests previously named individuals, which tied them to one roster and one
    export: the same test would fail on a dataset where that person had no
    business, without anything being wrong.

    Ordered by name rather than by budget size, deliberately. Ranking by size
    made the choice depend on budgets that other tests had just changed, so the
    same rank returned a different manager depending on execution order — and
    the resulting failures looked like defects rather than test interference.

    Restricted to managers carrying a budget in every month that has one, and a
    non-zero one. Callers pair this with budget_months() and then assert on each
    month in turn, which silently assumed the two lists formed a full grid. They
    do not: a manager can hold business in one month and none in another, and a
    manager whose forecast is zero has a budget of zero that no percentage can
    move. Both produced failures that read as defects in budget control while
    the figures were correct — the first as a missing row, the second as
    0.00 not being greater than 0.00.
    """
    with conn.cursor() as cur:
        cur.execute("""SELECT canonical_manager FROM v_monthly_budget
                       GROUP BY 1
                       HAVING COUNT(DISTINCT forecast_month)
                              = (SELECT COUNT(DISTINCT forecast_month)
                                 FROM v_monthly_budget)
                          AND SUM(total_budget) > 0
                       ORDER BY canonical_manager""")
        names = [r[0] for r in cur.fetchall()]
    if len(names) <= rank:
        import pytest as _pytest
        _pytest.skip("not enough managers budgeted in every month in this dataset")
    return names[rank]
