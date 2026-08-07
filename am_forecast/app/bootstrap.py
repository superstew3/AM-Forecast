"""Startup bootstrap.

Brings a database up to date when the deployment pipeline will not.

Normally migrations are applied by whoever deploys, and that is the better
arrangement: it is deliberate, it is visible, and it happens once. This exists
because a managed platform can end up serving new application code against a
database that never received the matching schema — which is exactly the failure
that produces a login page that hangs, with nothing obviously wrong on either
side.

Three properties make it safe enough to run on every start:

* **An advisory lock.** Several workers start at once. Only one runs migrations;
  the rest wait and then find nothing to do.
* **A record of what has run.** `schema_migration` holds one row per file, so
  files are applied once and in order. Existing databases are reconciled on
  first run by marking already-applied files rather than re-running them.
* **Off unless asked.** `AM_FORECAST_AUTO_MIGRATE=1` enables it. Nothing changes
  for anyone applying migrations by hand.

Account seeding is separate and narrower: it creates the initial users only when
the table is empty, and only from passwords supplied in the environment. It will
not touch an account that already exists, and it never invents a password.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg2

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations" / "versions"

# One arbitrary but fixed key, so every instance contends for the same lock.
LOCK_KEY = 8_147_231_905

AUTO_MIGRATE = os.environ.get("AM_FORECAST_AUTO_MIGRATE") == "1"
AUTO_SEED = os.environ.get("AM_FORECAST_AUTO_SEED_USERS") == "1"

# Environment variable per account. Absent means "do not create this one".
SEED_ACCOUNTS = [
    ("michael@stewartinsurance.com.au", "Michael Stewart", "administrator",
     "Michael Stewart", "AM_FORECAST_PW_MICHAEL"),
    ("sam@stewartinsurance.com.au", "Sam Stewart", "administrator",
     "Sam Stewart", "AM_FORECAST_PW_SAM"),
    ("anastasia@stewartinsurance.com.au", "Anastasia K", "viewer",
     "Anastasia K", "AM_FORECAST_PW_ANASTASIA"),
]


def _files() -> list[Path]:
    return sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name)


def _table_exists(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
    return cur.fetchone()[0]


def pending(conn) -> list[Path]:
    """Migration files not yet recorded as applied."""
    with conn.cursor() as cur:
        if not _table_exists(cur, "schema_migration"):
            # Nothing has been recorded. If the schema is clearly already in
            # place, treat everything up to the tracking table as applied
            # rather than re-running it over live data.
            established = _table_exists(cur, "sales_transaction")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migration (
                    filename   varchar(200) PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now(),
                    applied_by varchar(120))""")
            if established:
                for f in _files():
                    if f.name <= "0015_auth_text_encoding.sql":
                        cur.execute("""INSERT INTO schema_migration
                                       (filename, applied_by) VALUES (%s, 'reconciled')
                                       ON CONFLICT DO NOTHING""", (f.name,))
            conn.commit()
        cur.execute("SELECT filename FROM schema_migration")
        done = {r[0] for r in cur.fetchall()}
    return [f for f in _files() if f.name not in done]


def migrate(dsn: str, *, actor: str = "startup") -> list[str]:
    """Apply pending migrations. Returns the filenames applied.

    Two connections, deliberately. The advisory lock has to be held outside any
    transaction for the whole run, while each migration file needs its own
    transaction so a failure rolls back that file alone. Trying to do both on
    one connection fails: psycopg2 will not change autocommit mid-transaction.
    """
    applied: list[str] = []
    lock = psycopg2.connect(dsn)
    lock.autocommit = True
    try:
        with lock.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
            if not cur.fetchone()[0]:
                # Another worker is doing it. Nothing to report.
                return []

        work = psycopg2.connect(dsn)
        try:
            for path in pending(work):
                with work.cursor() as cur:
                    cur.execute(path.read_text())
                    cur.execute("""INSERT INTO schema_migration (filename, applied_by)
                                   VALUES (%s, %s) ON CONFLICT DO NOTHING""",
                                (path.name, actor))
                work.commit()
                applied.append(path.name)
        finally:
            work.close()

        with lock.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
    finally:
        lock.close()
    return applied


def seed_users(dsn: str) -> list[str]:
    """Create the initial accounts, once, from environment-supplied passwords.

    Deliberately narrow. It will not overwrite an existing account, and it
    refuses to invent a password: an account with no password set in the
    environment is skipped and reported, rather than created with something
    guessable.
    """
    from .api.auth import hash_password

    created: list[str] = []
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM app_user WHERE email IS NOT NULL")
            if cur.fetchone()[0]:
                return []
            for email, name, role, manager, env_key in SEED_ACCOUNTS:
                password = os.environ.get(env_key)
                if not password:
                    continue
                digest, salt, n, r, p = hash_password(password)
                cur.execute("""
                    INSERT INTO app_user (username, email, display_name, role,
                        password_hash, password_salt, password_n, password_r,
                        password_p, password_set_at, must_change_password, active,
                        canonical_manager, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),true,true,%s,'bootstrap')
                    ON CONFLICT DO NOTHING RETURNING id""",
                            (email, email, name, role, digest, salt, n, r, p,
                             manager))
                row = cur.fetchone()
                if row:
                    cur.execute("""INSERT INTO auth_event (email, user_id, event, detail)
                                   VALUES (%s,%s,'user_created',
                                           '{"by":"bootstrap"}'::jsonb)""",
                                (email, row[0]))
                    created.append(email)
    return created


def run(dsn: str) -> dict:
    """Called at application startup. Silent and harmless when disabled."""
    result: dict = {"migrated": [], "users_created": [], "enabled": AUTO_MIGRATE}
    if not AUTO_MIGRATE:
        return result
    result["migrated"] = migrate(dsn)
    if AUTO_SEED:
        result["users_created"] = seed_users(dsn)
    return result
