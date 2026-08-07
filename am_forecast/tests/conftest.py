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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


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
