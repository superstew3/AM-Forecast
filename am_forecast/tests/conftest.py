import sys
from pathlib import Path

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os
from pathlib import Path

# Source fixtures live outside the repo. Override with
# AM_FORECAST_FIXTURES; tests needing them skip when absent.
FIXTURE_DIR = Path(os.environ.get("AM_FORECAST_FIXTURES",
                                  "/mnt/user-data/uploads"))
SALES_FILE = str(FIXTURE_DIR / "Sales_Transaction_List_25-26.csv")
RENEWALS_FILE = str(FIXTURE_DIR / "Renewals_Pending_Summary_-_now-june2027.csv")


def pytest_collection_modifyitems(config, items):
    """Skip fixture-dependent tests when the source files are absent.

    These tests re-import the raw CSVs to exercise the import and
    matching paths. Without the files they cannot run, and skipping
    is more honest than erroring.
    """
    if FIXTURE_DIR.is_dir():
        return
    skip = pytest.mark.skip(
        reason=f"source fixtures not found at {FIXTURE_DIR}; "
               "set AM_FORECAST_FIXTURES")
    for item in items:
        if item.module.__name__.split(".")[-1] in (
                "test_stage2_import", "test_stage3_forecast",
                "test_stage4_matching"):
            item.add_marker(skip)


def pytest_addoption(parser):
    parser.addoption("--dsn", action="store", default="dbname=am_forecast")


@pytest.fixture(scope="session")
def conn(request):
    c = psycopg2.connect(request.config.getoption("--dsn"))
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clean_transaction(conn):
    """Reset the connection between tests so one failure cannot cascade."""
    yield
    conn.rollback()
