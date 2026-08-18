#!/usr/bin/env python3
"""Create or reset a user account.

Passwords are generated here rather than chosen, because a password chosen under
time pressure is usually a weak one. The generated form is four unrelated words
plus a number and a symbol: long enough to resist guessing, short enough to type,
and memorable enough that nobody writes it on a sticky note.

Every account created this way is flagged `must_change_password`, so the
generated password only survives until first sign-in.

    python scripts/create_users.py <dsn> --list
    python scripts/create_users.py <dsn> --create email@example.com "Display Name" role
    python scripts/create_users.py <dsn> --reset email@example.com
"""
from __future__ import annotations

import secrets
import sys
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.api.auth import hash_password  # noqa: E402

# Deliberately ordinary words: unrelated, easy to type, no ambiguous spellings.
WORDS = """
anchor amber ballast beacon bramble cactus canvas cedar cinder cobalt compass
copper coral cotton crimson cypress dahlia delta driftwood ember fathom fennel
flint galley granite harbour hazel heron indigo ironbark jasper juniper kestrel
lantern lattice linen lupin mackerel mangrove marlin meadow mica mulberry
nautical nectar oakum ochre osprey paddock pelican pewter pinnacle quartz quay
quiver rafter rattan rigging rosella rudder saffron sandbar sextant shale
shearwater sienna slipway spinnaker starling tallow teakwood thistle tidal
timber topsail trawler tundra umber vellum verdant wattle willow windlass yarrow
""".split()

SYMBOLS = "!$%&*+?@"


def generate_password(words: int = 4) -> str:
    """Four unrelated words, a digit pair and a symbol.

    Roughly 4 x log2(120) + log2(100) + log2(8) bits of entropy, which is well
    past the point where guessing is the attacker's best move.
    """
    picked = [secrets.choice(WORDS).capitalize() for _ in range(words)]
    return ("-".join(picked)
            + str(secrets.randbelow(90) + 10)
            + secrets.choice(SYMBOLS))


def upsert(conn, email: str, display_name: str, role: str,
           canonical_manager: str | None, created_by: str,
           password: str | None = None) -> tuple[str, bool]:
    password = password or generate_password()
    digest, salt, n, r, p = hash_password(password)
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM app_user WHERE lower(email) = lower(%s)", (email,))
        existing = cur.fetchone()
        if existing:
            cur.execute("""
                UPDATE app_user SET display_name=%s, role=%s, password_hash=%s,
                       password_salt=%s, password_n=%s, password_r=%s, password_p=%s,
                       password_set_at=now(), must_change_password=true,
                       failed_attempts=0, locked_until=NULL, active=true,
                       canonical_manager=%s, updated_at=now()
                WHERE id=%s""",
                        (display_name, role, digest, salt, n, r, p,
                         canonical_manager, existing[0]))
            # A reset must end any session opened with the old password.
            cur.execute("""UPDATE user_session SET revoked_at=now(),
                           revoke_reason='password reset'
                           WHERE user_id=%s AND revoked_at IS NULL""", (existing[0],))
            cur.execute("""INSERT INTO auth_event (email, user_id, event, detail)
                           VALUES (%s,%s,'password_reset_by_admin',%s)""",
                        (email, existing[0], psycopg2.extras.Json(
                            {"by": created_by})))
            return password, False
        cur.execute("""
            INSERT INTO app_user (username, email, display_name, role, password_hash,
                password_salt, password_n, password_r, password_p, password_set_at,
                must_change_password, active, canonical_manager, created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),true,true,%s,%s)
            RETURNING id""",
                    (email, email, display_name, role, digest, salt, n, r, p,
                     canonical_manager, created_by))
        user_id = cur.fetchone()[0]
        cur.execute("""INSERT INTO auth_event (email, user_id, event, detail)
                       VALUES (%s,%s,'user_created',%s)""",
                    (email, user_id, psycopg2.extras.Json({"role": role,
                                                           "by": created_by})))
        return password, True


# email, display name, role, linked account manager
INITIAL_USERS = [
    ("michael@stewartinsurance.com.au", "Michael Stewart", "administrator",
     "Michael Stewart"),
    ("sam@stewartinsurance.com.au", "Sam Stewart", "administrator", "Sam Stewart"),
    ("anastasia@stewartinsurance.com.au", "Anastasia K", "viewer", "Anastasia K"),
]


def main() -> int:
    import psycopg2.extras  # noqa: F401  (used by upsert)
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    dsn = sys.argv[1]
    args = sys.argv[2:]

    with psycopg2.connect(dsn) as conn:
        if "--list" in args:
            with conn.cursor() as cur:
                cur.execute("""SELECT email, display_name, role, active,
                                      must_change_password, last_login_at
                               FROM app_user ORDER BY email""")
                for row in cur.fetchall():
                    print(f"  {row[0]:<40} {row[2]:<14} "
                          f"{'active' if row[3] else 'disabled':<9} "
                          f"{'must change password' if row[4] else ''}")
            return 0

        if "--create" in args:
            i = args.index("--create")
            email, name, role = args[i + 1], args[i + 2], args[i + 3]
            password, created = upsert(conn, email, name, role, None, "cli")
            print(f"{'created' if created else 'reset'}: {email}\n  password: {password}")
            return 0

        if "--set" in args:
            # Set a known password, for the case where the credential has
            # already been communicated by another route.
            i = args.index("--set")
            email, password = args[i + 1], args[i + 2]
            with conn.cursor() as cur:
                cur.execute("""SELECT display_name, role, canonical_manager
                               FROM app_user WHERE lower(email)=lower(%s)""", (email,))
                row = cur.fetchone()
            if not row:
                raise SystemExit(f"no account for {email}")
            upsert(conn, email, row[0], row[1], row[2], "cli", password)
            print(f"set: {email} (must change at first sign-in)")
            return 0

        if "--reset" in args:
            email = args[args.index("--reset") + 1]
            with conn.cursor() as cur:
                cur.execute("""SELECT display_name, role, canonical_manager
                               FROM app_user WHERE lower(email)=lower(%s)""", (email,))
                row = cur.fetchone()
            if not row:
                raise SystemExit(f"no account for {email}")
            password, _ = upsert(conn, email, row[0], row[1], row[2], "cli")
            print(f"reset: {email}\n  password: {password}")
            return 0

        # Default: create the initial three accounts.
        print("Initial accounts\n" + "=" * 72)
        for email, name, role, manager in INITIAL_USERS:
            password, created = upsert(conn, email, name, role, manager, "setup")
            print(f"{email}")
            print(f"  name      {name}")
            print(f"  role      {role}")
            print(f"  password  {password}")
            print(f"  status    {'created' if created else 'password reset'}, "
                  "must change on first sign-in")
            print()
        print("Each password works once. The application requires a new one at "
              "first sign-in.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
