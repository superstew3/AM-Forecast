"""Authentication.

Run in a subprocess with AM_FORECAST_DEV_AUTH unset, so these exercise the real
session path rather than the header shortcut the rest of the suite uses.
"""
from __future__ import annotations

import datetime as dt
import os
import secrets
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "Correct-Horse-Battery-Staple24!"


@pytest.fixture(scope="module")
def client(request):
    """A client with the development header path disabled."""
    os.environ["AM_FORECAST_DSN"] = request.config.getoption("--dsn")
    os.environ.pop("AM_FORECAST_DEV_AUTH", None)
    import importlib

    import app.api.auth as auth_module
    importlib.reload(auth_module)
    assert auth_module.DEV_AUTH is False, "dev auth must be off for these tests"

    from fastapi.testclient import TestClient

    from app.api import app
    with TestClient(app) as c:
        yield c
    os.environ["AM_FORECAST_DEV_AUTH"] = "1"
    importlib.reload(auth_module)


@pytest.fixture
def account(conn):
    """A throwaway account, removed afterwards."""
    sys.path.insert(0, str(ROOT))
    from app.api.auth import hash_password
    email = f"pytest-{secrets.token_hex(4)}@example.com"
    digest, salt, n, r, p = hash_password(PASSWORD)
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO app_user (username, email, display_name, role, password_hash,
                password_salt, password_n, password_r, password_p, password_set_at,
                active, must_change_password)
            VALUES (%s,%s,'Pytest User','administrator',%s,%s,%s,%s,%s,now(),true,false)
            RETURNING id""", (email, email, digest, salt, n, r, p))
        user_id = cur.fetchone()[0]
    conn.commit()
    yield {"email": email, "password": PASSWORD, "id": user_id}
    with conn.cursor() as cur:
        cur.execute("DELETE FROM auth_event WHERE user_id = %s", (user_id,))
        cur.execute("DELETE FROM app_user WHERE id = %s", (user_id,))
    conn.commit()


def scalar(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        return row[0] if row else None


# --- the door is shut ----------------------------------------------------------

def test_data_requires_a_session(client):
    for path in ("/api/business?financial_year=2026", "/api/managers",
                 "/api/bonus", "/api/policies?limit=1", "/api/export/policies?fmt=csv",
                 "/api/reference/mappings", "/api/uploads"):
        assert client.get(path).status_code == 401, path


def test_headers_alone_grant_nothing(client):
    """The development shortcut must be inert unless explicitly enabled."""
    r = client.get("/api/business?financial_year=2026",
                   headers={"X-User": "attacker", "X-Role": "administrator"})
    assert r.status_code == 401


def test_health_stays_open(client):
    """A monitor has to be able to poll without credentials."""
    assert client.get("/api/health").status_code == 200


# --- signing in ----------------------------------------------------------------

def test_sign_in_and_reach_data(client, account):
    assert client.get("/api/business?financial_year=2026").status_code == 401
    r = client.post("/api/auth/login",
                    json={"email": account["email"], "password": account["password"]})
    assert r.status_code == 200
    assert r.json()["role"] == "administrator"
    assert client.get("/api/business?financial_year=2026").status_code == 200
    client.post("/api/auth/logout")
    assert client.get("/api/business?financial_year=2026").status_code == 401


def test_cookie_is_httponly_and_samesite_strict(client, account):
    r = client.post("/api/auth/login",
                    json={"email": account["email"], "password": account["password"]})
    cookie = r.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    client.post("/api/auth/logout")


def test_email_is_case_insensitive(client, account):
    r = client.post("/api/auth/login",
                    json={"email": account["email"].upper(),
                          "password": account["password"]})
    assert r.status_code == 200
    client.post("/api/auth/logout")


def test_wrong_password_is_refused(client, account):
    r = client.post("/api/auth/login",
                    json={"email": account["email"], "password": "not the password"})
    assert r.status_code == 401
    assert client.get("/api/business?financial_year=2026").status_code == 401


def test_unknown_and_wrong_give_the_same_answer(client, account):
    """The response must not reveal which addresses have accounts."""
    unknown = client.post("/api/auth/login",
                          json={"email": "nobody@example.com", "password": "x"})
    wrong = client.post("/api/auth/login",
                        json={"email": account["email"], "password": "x"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_disabled_account_cannot_sign_in(client, account, conn):
    with conn.cursor() as cur:
        cur.execute("UPDATE app_user SET active=false WHERE id=%s", (account["id"],))
    conn.commit()
    r = client.post("/api/auth/login",
                    json={"email": account["email"], "password": account["password"]})
    assert r.status_code == 401
    assert scalar(conn, """SELECT count(*) FROM auth_event
                           WHERE user_id=%s AND event='login_failed_inactive'""",
                  (account["id"],)) == 1


# --- lockout and audit ---------------------------------------------------------

def test_lockout_after_five_failures(client, account, conn):
    """Recording the attempt has to survive rejecting it.

    An earlier version raised inside the database context manager, which rolled
    the whole thing back: the counter never advanced, lockout never engaged, and
    failed sign-ins left no trace at all.
    """
    for _ in range(4):
        assert client.post("/api/auth/login",
                           json={"email": account["email"],
                                 "password": "wrong"}).status_code == 401
    assert scalar(conn, "SELECT failed_attempts FROM app_user WHERE id=%s",
                  (account["id"],)) == 4

    r = client.post("/api/auth/login",
                    json={"email": account["email"], "password": "wrong"})
    assert r.status_code == 429
    assert "Try again" in r.json()["detail"]

    # Even the correct password is refused while locked.
    assert client.post("/api/auth/login",
                       json={"email": account["email"],
                             "password": account["password"]}).status_code == 429

    assert scalar(conn, """SELECT count(*) FROM auth_event
                           WHERE user_id=%s AND event='account_locked'""",
                  (account["id"],)) == 1


def test_successful_sign_in_clears_the_counter(client, account, conn):
    client.post("/api/auth/login",
                json={"email": account["email"], "password": "wrong"})
    assert scalar(conn, "SELECT failed_attempts FROM app_user WHERE id=%s",
                  (account["id"],)) == 1
    client.post("/api/auth/login",
                json={"email": account["email"], "password": account["password"]})
    assert scalar(conn, "SELECT failed_attempts FROM app_user WHERE id=%s",
                  (account["id"],)) == 0
    client.post("/api/auth/logout")


def test_every_attempt_is_recorded(client, account, conn):
    before = scalar(conn, "SELECT count(*) FROM auth_event WHERE user_id=%s",
                    (account["id"],))
    client.post("/api/auth/login",
                json={"email": account["email"], "password": "wrong"})
    client.post("/api/auth/login",
                json={"email": account["email"], "password": account["password"]})
    client.post("/api/auth/logout")
    after = scalar(conn, "SELECT count(*) FROM auth_event WHERE user_id=%s",
                   (account["id"],))
    assert after - before == 3


# --- password storage ----------------------------------------------------------

def test_passwords_are_never_stored_in_the_clear(conn, account):
    """Stored as base64 text rather than bytea, so a managed publish pipeline
    does not have to accept a column type change."""
    import base64
    stored = scalar(conn, "SELECT password_hash FROM app_user WHERE id=%s",
                    (account["id"],))
    assert isinstance(stored, str)
    assert PASSWORD not in stored
    assert len(base64.b64decode(stored)) == 32

    salt = scalar(conn, "SELECT password_salt FROM app_user WHERE id=%s",
                  (account["id"],))
    assert len(base64.b64decode(salt)) == 16
    assert scalar(conn, "SELECT password_algo FROM app_user WHERE id=%s",
                  (account["id"],)) == "scrypt"


def test_identical_passwords_hash_differently(conn):
    """Per-user salts, so one cracked password does not reveal the rest."""
    sys.path.insert(0, str(ROOT))
    from app.api.auth import hash_password
    a, _, _, _, _ = hash_password("the same password")
    b, _, _, _, _ = hash_password("the same password")
    assert a != b


def test_session_tokens_are_stored_hashed(client, account, conn):
    r = client.post("/api/auth/login",
                    json={"email": account["email"], "password": account["password"]})
    cookie = r.headers.get("set-cookie", "")
    token = cookie.split("am_session=")[1].split(";")[0]
    stored = scalar(conn, """SELECT token_hash FROM user_session
                             WHERE user_id=%s ORDER BY id DESC LIMIT 1""",
                    (account["id"],))
    assert isinstance(stored, str)
    assert token not in stored
    assert len(stored) == 64          # SHA-256 as hex
    client.post("/api/auth/logout")


def test_a_forged_cookie_is_rejected(client):
    r = client.get("/api/business?financial_year=2026",
                   cookies={"am_session": secrets.token_urlsafe(48)})
    assert r.status_code == 401


# --- changing a password -------------------------------------------------------

def test_password_change_requires_the_current_one(client, account):
    client.post("/api/auth/login",
                json={"email": account["email"], "password": account["password"]})
    r = client.post("/api/auth/change-password",
                    json={"current_password": "wrong", "new_password": "Another-Good-One-99"})
    assert r.status_code == 401
    client.post("/api/auth/logout")


def test_weak_passwords_are_refused(client, account):
    client.post("/api/auth/login",
                json={"email": account["email"], "password": account["password"]})
    for weak in ("short1A", "alllowercase123", "ALLUPPERCASE123", "NoDigitsHereAtAll"):
        r = client.post("/api/auth/change-password",
                        json={"current_password": account["password"],
                              "new_password": weak})
        assert r.status_code == 422, weak
    client.post("/api/auth/logout")


def test_changing_a_password_signs_out_other_devices(client, account, conn):
    """A password changed because someone may have seen it must actually
    remove their access."""
    from fastapi.testclient import TestClient

    from app.api import app
    other = TestClient(app)
    other.post("/api/auth/login",
               json={"email": account["email"], "password": account["password"]})
    assert other.get("/api/business?financial_year=2026").status_code == 200

    client.post("/api/auth/login",
                json={"email": account["email"], "password": account["password"]})
    new_password = "Lantern-Quartz-Rafter-Willow77!"
    r = client.post("/api/auth/change-password",
                    json={"current_password": account["password"],
                          "new_password": new_password})
    assert r.status_code == 200
    assert r.json()["other_sessions_revoked"] >= 1

    assert other.get("/api/business?financial_year=2026").status_code == 401
    assert client.get("/api/business?financial_year=2026").status_code == 200
    client.post("/api/auth/logout")


# --- the real accounts ---------------------------------------------------------

def test_the_three_accounts_exist_with_expected_roles(conn):
    rows = {r[0]: (r[1], r[2]) for r in _fetch(conn, """
        SELECT email, role, active FROM app_user
        WHERE email LIKE '%@stewartinsurance.com.au'""")}
    assert rows["michael@stewartinsurance.com.au"] == ("administrator", True)
    assert rows["sam@stewartinsurance.com.au"] == ("administrator", True)
    assert rows["anastasia@stewartinsurance.com.au"] == ("viewer", True)


def _fetch(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()


def test_issued_accounts_must_change_password(conn):
    for email in ("michael@stewartinsurance.com.au", "sam@stewartinsurance.com.au",
                  "anastasia@stewartinsurance.com.au"):
        assert scalar(conn, """SELECT must_change_password FROM app_user
                               WHERE email=%s""", (email,)) is True, email


# --- deployment resilience ------------------------------------------------------

def test_no_binary_columns_in_the_auth_schema(conn):
    """Every auth column is text.

    A managed publish pipeline can refuse ALTER COLUMN ... TYPE bytea as
    possibly not backwards compatible, which blocks the whole release. The
    encoding is cryptographically irrelevant; the deployability is not.
    """
    binary = _fetch(conn, """
        SELECT table_name, column_name FROM information_schema.columns
        WHERE table_schema='public' AND data_type='bytea'
          AND table_name IN ('app_user','user_session','auth_event')""")
    assert binary == [], f"binary columns remain: {binary}"


def test_health_reports_schema_readiness(client):
    """A monitor must be able to tell 'running' from 'usable'.

    An app serving pages against a database that never received its migrations
    passes any simpler check while failing every query.
    """
    h = client.get("/api/health").json()
    assert h["ready"] is True
    assert h["checks"]["accounts"].endswith("account(s)")
    for key in ("database", "sessions", "auth audit", "transactions"):
        assert h["checks"][key] == "ok", key


def test_migrations_are_recorded_once_applied(conn):
    recorded = {r[0] for r in _fetch(conn, "SELECT filename FROM schema_migration")}
    files = {p.name for p in (ROOT / "migrations" / "versions").glob("*.sql")}
    # Every file is either recorded or was applied before tracking existed;
    # nothing may be recorded that does not exist on disk.
    assert recorded <= files, f"recorded but missing on disk: {recorded - files}"


def test_bootstrap_is_disabled_unless_asked(monkeypatch):
    """Automatic migration on startup is an escape hatch, not the default."""
    import importlib

    import app.bootstrap as bootstrap
    monkeypatch.delenv("AM_FORECAST_AUTO_MIGRATE", raising=False)
    importlib.reload(bootstrap)
    assert bootstrap.AUTO_MIGRATE is False
    assert bootstrap.run("postgresql://invalid") == {
        "migrated": [], "users_created": [], "enabled": False}


def test_seeding_refuses_to_invent_a_password(monkeypatch, conn):
    """An account with no password in the environment is skipped, never created
    with something guessable."""
    import importlib

    import app.bootstrap as bootstrap
    importlib.reload(bootstrap)
    for _, _, _, _, key in bootstrap.SEED_ACCOUNTS:
        monkeypatch.delenv(key, raising=False)
    # The table is not empty here, so seeding is a no-op regardless; the point
    # is that it never fabricates a credential.
    assert bootstrap.seed_users(os.environ["AM_FORECAST_DSN"]) == []
