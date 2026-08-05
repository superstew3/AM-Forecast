import sys
from pathlib import Path

import psycopg2
import pytest

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
