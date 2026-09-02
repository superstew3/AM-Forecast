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

Account seeding is separate, narrower, and **independently switched**. It creates
the initial users only when none exist, and only from passwords supplied in the
environment. It will not touch an account that already exists, and it never
invents a password.

The two are independent on purpose. Seeding is an ordinary INSERT — no schema
change, nothing a platform guardrail should object to — whereas automatic
migration is DDL at startup, which many managed platforms forbid outright and
reasonably so. An earlier version made seeding depend on the migration flag,
which meant a deployment allowed to insert rows but not to run DDL could not
create its first account at all: the only remaining route was hand-editing the
production database. Coupling a harmless operation to a restricted one leaves no
safe path.
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
        # An EMPTY tracking table is the same situation as a missing one.
        #
        # It only reconciled when the table did not exist. rebuild.sh applies
        # every migration with a plain `psql -f` and records none of them, so a
        # rebuilt database ends up with the table present and nothing in it --
        # and pending() then reports all twenty-four files as un-applied against
        # a schema that already has them.
        #
        # With auto-migrate on, that replays 0001 onward over a populated
        # database. It is the same fault as the hardcoded 0015 watermark that
        # silently dropped bonus_gst_divisor, reached through a different door:
        # one asked the wrong question about WHICH files were applied, this one
        # about WHETHER anything had been recorded at all.
        recorded = 0
        if _table_exists(cur, "schema_migration"):
            cur.execute("SELECT count(*) FROM schema_migration")
            recorded = cur.fetchone()[0]
        if not _table_exists(cur, "schema_migration") or recorded == 0:
            # Nothing has been recorded. If the schema is clearly already in
            # place, treat every migration file that currently exists as
            # applied, not just the ones up to some filename typed in here.
            # A database with a populated sales_transaction has had ALL of
            # its migrations run -- a hardcoded cutoff (this used to stop at
            # "0015_auth_text_encoding.sql") means every migration added
            # after that point is never reconciled: pending() keeps treating
            # it as un-applied forever, and migrate() replays its raw SQL on
            # every startup that has AUTO_MIGRATE on. That is exactly what
            # silently dropped bonus_gst_divisor after it had already been
            # applied by hand -- migrate() re-ran 0016 onward from scratch,
            # including 0019, but a later migration file recreated the
            # column on a schema state that no longer matched.
            established = _table_exists(cur, "sales_transaction")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS schema_migration (
                    filename   varchar(200) PRIMARY KEY,
                    applied_at timestamptz NOT NULL DEFAULT now(),
                    applied_by varchar(120))""")
            if established:
                for f in _files():
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


def seed_users(dsn: str) -> tuple[list[str], str | None]:
    """Create the initial accounts, once, from environment-supplied passwords.

    Returns the addresses created and, where nothing was created, a note saying
    why. Silence is unhelpful here: an empty result could mean "already done",
    "no passwords supplied" or "the table does not exist", and those need
    different responses.

    Deliberately narrow. It will not overwrite an existing account, and it
    refuses to invent a password.
    """
    from .api.auth import hash_password

    created: list[str] = []
    with psycopg2.connect(dsn) as conn:
        with conn.cursor() as cur:
            if not _table_exists(cur, "app_user"):
                return [], ("app_user does not exist: migrations have not been "
                            "applied to this database")
            cur.execute("""SELECT count(*) FROM information_schema.columns
                           WHERE table_name='app_user' AND column_name='email'""")
            if not cur.fetchone()[0]:
                return [], ("app_user has no email column: the authentication "
                            "migration has not been applied to this database")

            cur.execute("SELECT count(*) FROM app_user WHERE email IS NOT NULL")
            if cur.fetchone()[0]:
                return [], "accounts already exist; seeding skipped"

            supplied = [k for _, _, _, _, k in SEED_ACCOUNTS if os.environ.get(k)]
            if not supplied:
                return [], ("no passwords supplied; set "
                            + ", ".join(k for _, _, _, _, k in SEED_ACCOUNTS))
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
    skipped = [e for e, _, _, _, k in SEED_ACCOUNTS
               if not os.environ.get(k)]
    note = (f"skipped (no password supplied): {', '.join(skipped)}"
            if skipped else None)
    return created, note


def run(dsn: str) -> dict:
    """Called at application startup. Silent and harmless when both are off.

    The two steps are gated separately. Seeding must be reachable on a
    deployment that permits inserts but forbids startup DDL, which is the normal
    posture for a managed platform.
    """
    result: dict = {
        "migrated": [], "users_created": [], "notes": [],
        "auto_migrate": AUTO_MIGRATE, "auto_seed": AUTO_SEED,
    }
    if AUTO_MIGRATE:
        result["migrated"] = migrate(dsn)
    if AUTO_SEED:
        created, note = seed_users(dsn)
        result["users_created"] = created
        if note:
            result["notes"].append(note)
    return result
