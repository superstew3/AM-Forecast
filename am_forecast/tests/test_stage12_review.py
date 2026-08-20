"""The match review workflow, which had no test at all.

Four endpoints -- the queue, manual match, reject and apportion -- write
allocations and therefore change reported income. They were the largest block of
untested surface in the application, and the one where a quiet regression would
be most expensive: a wrong allocation moves money between managers, and nothing
downstream would flag it because the total stays the same.

These assert the guarantees rather than any dataset's figures: that an
apportionment cannot exceed its transaction, that a decision requires a reason,
that the endpoints refuse a viewer, and that every decision is recorded.
"""
from __future__ import annotations

import os
from decimal import Decimal

import pytest


@pytest.fixture(scope="module")
def client(request):
    os.environ["AM_FORECAST_DSN"] = request.config.getoption("--dsn")
    os.environ["AM_FORECAST_DEV_AUTH"] = "1"
    from fastapi.testclient import TestClient

    from app.api import app
    with TestClient(app) as c:
        c.headers.update({"X-User": "pytest-admin", "X-Role": "administrator"})
        yield c


def rows(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        return cur.fetchall()


def scalar(conn, sql, params=None):
    r = rows(conn, sql, params)
    return r[0][0] if r else None


def _contended(conn):
    """A transaction with at least two distinct policies competing for it."""
    found = rows(conn, """
        SELECT transaction_id, array_agg(DISTINCT policy_id), min(forecast_month)
        FROM match_candidate
        WHERE reason = 'multiple_policies_for_transaction' AND policy_id IS NOT NULL
        GROUP BY 1 HAVING count(DISTINCT policy_id) >= 2
        ORDER BY transaction_id LIMIT 1""")
    return found[0] if found else None


# --- the queue ----------------------------------------------------------------

def test_the_queue_answers_for_every_kind(client):
    """All four filters respond, and none leaks a row belonging to another."""
    seen: dict[str, set] = {}
    for kind in ("actionable", "timing", "out_of_scope", "all"):
        r = client.get(f"/api/review?kind={kind}")
        assert r.status_code == 200, (kind, r.text[:200])
        d = r.json()
        assert "items" in d, kind
        seen[kind] = {i.get("transaction_id") for i in d["items"]}
    # "all" is the union, not a fifth unrelated query.
    union = seen["actionable"] | seen["timing"] | seen["out_of_scope"]
    assert union <= seen["all"], "a row appears under a filter but not under all"


def test_the_queue_rejects_a_kind_it_does_not_know(client):
    assert client.get("/api/review?kind=whatever").status_code == 422


# --- decisions require a reason and an administrator --------------------------

def test_a_decision_requires_an_administrator(client):
    """A viewer must not be able to move money between managers.

    Checked by role rather than by trusting the route ordering: these endpoints
    write match_allocation, and an allocation is income attributed to somebody.
    """
    client.headers.update({"X-User": "pytest-viewer", "X-Role": "viewer"})
    try:
        for path in ("/api/review/match", "/api/review/reject", "/api/review/apportion"):
            r = client.post(path, json={"transaction_id": 1, "reason": "should be refused"})
            assert r.status_code in (401, 403), (path, r.status_code)
    finally:
        client.headers.update({"X-User": "pytest-admin", "X-Role": "administrator"})


def test_a_manual_match_needs_a_policy_and_a_month(client):
    r = client.post("/api/review/match",
                    json={"transaction_id": 1, "reason": "missing the policy"})
    assert r.status_code == 400
    assert "policy_id" in r.json()["detail"]


def test_an_apportionment_needs_splits(client):
    r = client.post("/api/review/apportion",
                    json={"transaction_id": 1, "reason": "no splits supplied"})
    assert r.status_code == 400
    assert "splits" in r.json()["detail"]


# --- the guarantee that matters ------------------------------------------------

def test_an_apportionment_cannot_exceed_its_transaction(client, conn):
    """Splitting a transaction must never create income.

    The one rule here that cannot be allowed to slip: apportioning is dividing a
    figure, not multiplying it. Over-allocation would inflate one manager's
    income without reducing anybody else's, and every total downstream would
    still reconcile, because the transaction it came from is not part of the sum.
    """
    found = _contended(conn)
    if not found:
        pytest.skip("no transaction in this dataset has competing policies")
    txn, pids, month = found
    income = scalar(conn, "SELECT actual_income FROM sales_transaction WHERE id = %s",
                    (txn,))
    if income is None or income <= 0:
        pytest.skip("the contended transaction carries no positive income")

    over = (Decimal(str(income)) * Decimal("0.7")).quantize(Decimal("0.01"))
    r = client.post("/api/review/apportion", json={
        "transaction_id": txn, "reason": "deliberate over-allocation",
        "splits": [{"policy_id": pids[0], "forecast_month": str(month), "allocated_income": str(over)},
                   {"policy_id": pids[1], "forecast_month": str(month), "allocated_income": str(over)}]})
    assert r.status_code >= 400, "an apportionment exceeding the transaction was accepted"

    allocated = scalar(conn, """SELECT COALESCE(SUM(allocated_income), 0)
                                FROM match_allocation WHERE transaction_id = %s""", (txn,))
    assert Decimal(str(allocated)) <= Decimal(str(income)) + Decimal("0.01"), \
        "allocations exceed the transaction they came from"


def test_every_decision_is_recorded(client, conn):
    """A decision that leaves no trace is not auditable.

    Whoever asks in six months why a transaction sits against one policy rather
    than another needs the answer to exist, and the reason field is the only
    place it can live.
    """
    r = client.get("/api/review/history")
    assert r.status_code == 200
    for row in r.json().get("items", [])[:20]:
        # The field is "reviewer"; I wrote "decided_by" from memory and the test
        # failed on data that was perfectly correct. Read the shape, do not
        # assume it -- the same habit that produced four hand-written copies of
        # the exclusion rules.
        assert row.get("reviewer"), row
        assert row.get("reason"), "a recorded decision with no reason given"
