"""Authentication.

Decisions worth stating, since security choices are easy to get subtly wrong and
hard to notice afterwards.

**scrypt, not a bare hash.** Passwords are stored as scrypt hashes with a
per-user random salt and per-row cost parameters. scrypt is memory-hard, so a
stolen database resists the cheap parallel hardware that makes SHA-256 password
cracking trivial. Cost parameters live on the row, so they can be raised later
without invalidating anyone's password: the next successful login re-hashes at
the new cost.

**Session tokens are never stored.** Only a SHA-256 of the token is kept. A
database leak therefore does not hand over live sessions.

**The cookie is HttpOnly, SameSite=Strict, and Secure outside development.**
HttpOnly keeps the token out of reach of any script on the page. SameSite=Strict
is what makes CSRF tokens unnecessary here: the browser will not attach the
cookie to a request originating from another site.

**Lockout is per account, not per IP.** The realistic threat is someone guessing
a colleague's password, not a distributed botnet, and per-IP lockout would let
one office IP lock out everybody.

**Timing.** An unknown email still pays the cost of a hash comparison, so
response time does not reveal which addresses have accounts.
"""
from __future__ import annotations

import base64
import datetime as dt
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from dataclasses import dataclass

import psycopg2
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg2.extras import Json, RealDictCursor
from pydantic import BaseModel, EmailStr, Field

from .core import DSN, ROLES, User

router = APIRouter()

# --- policy ------------------------------------------------------------------

SESSION_COOKIE = "am_session"
SESSION_HOURS = 12
# Sliding window: a session in active use is extended, so a working day does not
# end with an unexpected logout, but an abandoned session still dies.
SESSION_IDLE_MINUTES = 90
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LENGTH = 12

SCRYPT_N = 2 ** 15          # 32 MiB, roughly 0.23s per hash on modest hardware
SCRYPT_R = 8
SCRYPT_P = 1
DK_LEN = 32

# Development convenience only. When unset (the default) the X-User and X-Role
# headers are ignored entirely and a real session is required.
DEV_AUTH = os.environ.get("AM_FORECAST_DEV_AUTH") == "1"


def _conn():
    return psycopg2.connect(DSN, cursor_factory=RealDictCursor)


# --- password hashing --------------------------------------------------------

def hash_password(password: str, *, n: int = SCRYPT_N, r: int = SCRYPT_R,
                  p: int = SCRYPT_P) -> tuple[str, str, int, int, int]:
    """Hash a password. Returns base64 text, not bytes.

    Text rather than binary because a managed deployment pipeline can refuse an
    ALTER COLUMN ... TYPE bytea as possibly not backwards compatible and block
    the release. base64 of the same 32 bytes of scrypt output is
    cryptographically identical and keeps the schema change additive.
    """
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p,
                            dklen=DK_LEN, maxmem=n * r * 256)
    return _b64(digest), _b64(salt), n, r, p


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def _unb64(value) -> bytes:
    """Decode stored material.

    Tolerates memoryview and bytes as well as str, so a database still holding
    the older binary columns keeps working until migration 0015 has run.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        try:
            return base64.b64decode(raw, validate=True)
        except Exception:
            return raw
    return base64.b64decode(value)


def verify_password(password: str, digest, salt, n: int, r: int, p: int) -> bool:
    digest_raw, salt_raw = _unb64(digest), _unb64(salt)
    candidate = hashlib.scrypt(password.encode(), salt=salt_raw, n=n, r=r, p=p,
                               dklen=len(digest_raw), maxmem=n * r * 256)
    # Constant time: a short-circuit comparison leaks how much of the hash
    # matched, which is enough to attack it byte by byte.
    return hmac.compare_digest(candidate, digest_raw)


PASSWORD_RULES = (
    f"At least {MIN_PASSWORD_LENGTH} characters, including a lower-case letter, "
    "an upper-case letter and a digit. Longer beats more complicated: a phrase "
    "of four unrelated words is both stronger and easier to remember than a "
    "short string of symbols."
)


def check_password_strength(password: str) -> None:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not re.search(r"[a-z]", password):
        problems.append("a lower-case letter")
    if not re.search(r"[A-Z]", password):
        problems.append("an upper-case letter")
    if not re.search(r"\d", password):
        problems.append("a digit")
    if problems:
        raise HTTPException(422, "Password needs " + ", ".join(problems) + ".")


# --- sessions ----------------------------------------------------------------

def _token_hash(token: str) -> str:
    """Hex SHA-256 of the token. The token itself is never stored."""
    return hashlib.sha256(token.encode()).hexdigest()


def _client(request: Request) -> tuple[str | None, str | None]:
    """Caller address and agent.

    The address is stored as `inet`, so anything that is not a real address —
    a hostname from a test client, or a malformed X-Forwarded-For — is dropped
    rather than allowed to fail the insert. Losing an audit field is bad;
    losing the audit row because of it would be worse.
    """
    raw = request.client.host if request.client else None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        raw = forwarded.split(",")[0].strip()
    ip = None
    if raw:
        try:
            ip = str(ipaddress.ip_address(raw))
        except ValueError:
            ip = None
    return ip, request.headers.get("user-agent")


def _record(cur, event: str, *, email: str | None = None, user_id: int | None = None,
            request: Request | None = None, detail: dict | None = None) -> None:
    ip, agent = _client(request) if request else (None, None)
    cur.execute("""
        INSERT INTO auth_event (email, user_id, event, ip, user_agent, detail)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (email, user_id, event, ip, agent, Json(detail) if detail else None))


def issue_session(cur, user_id: int, request: Request) -> tuple[str, dt.datetime]:
    token = secrets.token_urlsafe(48)
    expires = dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=SESSION_HOURS)
    ip, agent = _client(request)
    cur.execute("""
        INSERT INTO user_session (user_id, token_hash, expires_at, ip, user_agent)
        VALUES (%s, %s, %s, %s, %s)
    """, (user_id, _token_hash(token), expires, ip, agent))
    return token, expires


@dataclass(frozen=True)
class Session:
    user_id: int
    email: str
    display_name: str
    role: str
    must_change_password: bool
    canonical_manager: str | None
    expires_at: dt.datetime


def resolve_session(token: str) -> Session | None:
    """Validate a token and extend it if it is in active use."""
    now = dt.datetime.now(dt.timezone.utc)
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT s.id, s.user_id, s.expires_at, s.last_seen_at,
                       u.email, u.display_name, u.role, u.active,
                       u.must_change_password, u.canonical_manager
                FROM user_session s
                JOIN app_user u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.revoked_at IS NULL
            """, (_token_hash(token),))
            row = cur.fetchone()
            if row is None or not row["active"]:
                return None
            if row["expires_at"] <= now:
                cur.execute("""UPDATE user_session SET revoked_at = now(),
                               revoke_reason = 'expired' WHERE id = %s""", (row["id"],))
                _record(cur, "session_expired", email=row["email"],
                        user_id=row["user_id"])
                return None
            idle = now - row["last_seen_at"]
            if idle > dt.timedelta(minutes=SESSION_IDLE_MINUTES):
                cur.execute("""UPDATE user_session SET revoked_at = now(),
                               revoke_reason = 'idle' WHERE id = %s""", (row["id"],))
                _record(cur, "session_expired", email=row["email"],
                        user_id=row["user_id"], detail={"reason": "idle"})
                return None
            cur.execute("UPDATE user_session SET last_seen_at = now() WHERE id = %s",
                        (row["id"],))
            return Session(user_id=row["user_id"], email=row["email"],
                           display_name=row["display_name"], role=row["role"],
                           must_change_password=row["must_change_password"],
                           canonical_manager=row["canonical_manager"],
                           expires_at=row["expires_at"])


def _is_https(request: Request) -> bool:
    """Whether the browser reached us over HTTPS.

    Behind a proxy the scheme arrives in X-Forwarded-Proto; Replit and any
    normal deployment terminate TLS in front of the app.
    """
    forwarded = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip()
    return (forwarded or request.url.scheme) == "https"


def _set_cookie(response: Response, request: Request, token: str) -> None:
    """Set the session cookie.

    `Secure` follows the scheme the request actually arrived on. Hard-coding it
    true breaks local development over http — the browser silently refuses to
    store the cookie and the app looks broken for no visible reason. Hard-coding
    it false would send the token in clear over the wire in production. Deriving
    it is the only version that is right in both places.
    """
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=SESSION_HOURS * 3600,
        httponly=True,               # unreachable from any script on the page
        secure=_is_https(request),   # HTTPS only wherever HTTPS is in use
        samesite="strict",           # what makes a separate CSRF token unnecessary
        path="/",
    )


# --- endpoints ---------------------------------------------------------------

class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


@router.post("/auth/login", tags=["auth"])
def login(body: LoginBody, request: Request, response: Response):
    """Sign in.

    Every failure returns the same message and takes roughly the same time, so
    the response reveals nothing about which addresses have accounts.

    Note the shape: the outcome is decided and *committed* before any exception
    is raised. psycopg2's connection context manager rolls back when an
    exception leaves the block, so raising inside it silently discarded the
    failed-attempt counter and the audit row — lockout never engaged and failed
    sign-ins left no trace. Recording the attempt has to survive rejecting it.
    """
    generic = "Email or password is incorrect."
    email = body.email.strip().lower()
    now = dt.datetime.now(dt.timezone.utc)
    failure: HTTPException | None = None
    result: dict | None = None
    token = None

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, email, display_name, role, active, password_hash,
                       password_salt, password_n, password_r, password_p,
                       failed_attempts, locked_until, must_change_password,
                       canonical_manager
                FROM app_user WHERE lower(email) = %s
            """, (email,))
            user = cur.fetchone()

            if user is None:
                # Spend comparable time so an unknown address is not faster.
                hashlib.scrypt(body.password.encode(), salt=b"0" * 16, n=SCRYPT_N,
                               r=SCRYPT_R, p=SCRYPT_P, dklen=DK_LEN,
                               maxmem=SCRYPT_N * SCRYPT_R * 256)
                _record(cur, "login_failed_unknown_user", email=email, request=request)
                failure = HTTPException(401, generic)

            elif not user["active"]:
                _record(cur, "login_failed_inactive", email=email,
                        user_id=user["id"], request=request)
                failure = HTTPException(401, generic)

            elif user["locked_until"] and user["locked_until"] > now:
                _record(cur, "login_failed_locked", email=email, user_id=user["id"],
                        request=request)
                minutes = int((user["locked_until"] - now).total_seconds() // 60) + 1
                failure = HTTPException(
                    429, f"Too many failed attempts. Try again in {minutes} minute"
                         f"{'s' if minutes != 1 else ''}.")

            elif not (user["password_hash"] and verify_password(
                    body.password, user["password_hash"], user["password_salt"],
                    user["password_n"], user["password_r"], user["password_p"])):
                attempts = user["failed_attempts"] + 1
                lock_until = (now + dt.timedelta(minutes=LOCKOUT_MINUTES)
                              if attempts >= MAX_FAILED_ATTEMPTS else None)
                cur.execute("""UPDATE app_user SET failed_attempts = %s,
                               locked_until = %s WHERE id = %s""",
                            (attempts, lock_until, user["id"]))
                _record(cur, "login_failed_password", email=email, user_id=user["id"],
                        request=request, detail={"attempt": attempts})
                if lock_until:
                    _record(cur, "account_locked", email=email, user_id=user["id"],
                            request=request, detail={"minutes": LOCKOUT_MINUTES})
                    failure = HTTPException(
                        429, f"Too many failed attempts. Try again in "
                             f"{LOCKOUT_MINUTES} minutes.")
                else:
                    failure = HTTPException(401, generic)

            else:
                # Raise the stored cost silently if the policy has moved on.
                if user["password_n"] < SCRYPT_N:
                    digest, salt, n, r, p = hash_password(body.password)
                    cur.execute("""UPDATE app_user SET password_hash=%s,
                                   password_salt=%s, password_n=%s, password_r=%s,
                                   password_p=%s WHERE id=%s""",
                                (digest, salt, n, r, p, user["id"]))

                ip, _ = _client(request)
                cur.execute("""UPDATE app_user SET failed_attempts = 0,
                               locked_until = NULL, last_login_at = now(),
                               last_login_ip = %s WHERE id = %s""",
                            (ip, user["id"]))
                token, expires = issue_session(cur, user["id"], request)
                _record(cur, "login_success", email=email, user_id=user["id"],
                        request=request)
                result = {
                    "email": user["email"],
                    "display_name": user["display_name"],
                    "role": user["role"],
                    "canonical_manager": user["canonical_manager"],
                    "must_change_password": user["must_change_password"],
                    "expires_at": expires,
                }

    # Committed. Only now is it safe to reject.
    if failure is not None:
        raise failure
    _set_cookie(response, request, token)
    return result


@router.post("/auth/logout", tags=["auth"])
def logout(request: Request, response: Response):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE user_session SET revoked_at = now(),
                           revoke_reason = 'logout'
                    WHERE token_hash = %s AND revoked_at IS NULL
                    RETURNING user_id
                """, (_token_hash(token),))
                row = cur.fetchone()
                if row:
                    _record(cur, "logout", user_id=row["user_id"], request=request)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"status": "signed out"}


class ChangePasswordBody(BaseModel):
    current_password: str
    new_password: str


@router.post("/auth/change-password", tags=["auth"])
def change_password(body: ChangePasswordBody, request: Request):
    """Change your own password.

    Every other session for the account is revoked, so a password changed
    because it may have been seen by someone else actually removes their access.
    """
    session = require_session(request)
    check_password_strength(body.new_password)
    if body.new_password == body.current_password:
        raise HTTPException(422, "The new password must differ from the current one.")

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT password_hash, password_salt, password_n,
                                  password_r, password_p
                           FROM app_user WHERE id = %s""", (session.user_id,))
            row = cur.fetchone()
            if not verify_password(body.current_password, row["password_hash"],
                                   row["password_salt"], row["password_n"],
                                   row["password_r"], row["password_p"]):
                _record(cur, "login_failed_password", user_id=session.user_id,
                        email=session.email, request=request,
                        detail={"context": "change_password"})
                raise HTTPException(401, "Current password is incorrect.")

            digest, salt, n, r, p = hash_password(body.new_password)
            cur.execute("""UPDATE app_user SET password_hash=%s, password_salt=%s,
                           password_n=%s, password_r=%s, password_p=%s,
                           password_set_at=now(), must_change_password=false,
                           updated_at=now() WHERE id=%s""",
                        (digest, salt, n, r, p, session.user_id))
            current = request.cookies.get(SESSION_COOKIE)
            cur.execute("""UPDATE user_session SET revoked_at = now(),
                           revoke_reason = 'password changed'
                           WHERE user_id = %s AND revoked_at IS NULL
                             AND token_hash <> %s""",
                        (session.user_id, _token_hash(current) if current else b""))
            revoked = cur.rowcount
            _record(cur, "password_changed", user_id=session.user_id,
                    email=session.email, request=request,
                    detail={"other_sessions_revoked": revoked})
    return {"status": "password changed", "other_sessions_revoked": revoked,
            "note": "Any other device signed in as you has been signed out."}


@router.get("/auth/me", tags=["auth"])
def me(request: Request):
    session = require_session(request)
    return {
        "email": session.email,
        "display_name": session.display_name,
        "role": session.role,
        "canonical_manager": session.canonical_manager,
        "must_change_password": session.must_change_password,
        "expires_at": session.expires_at,
        "password_rules": PASSWORD_RULES,
    }


@router.get("/auth/events", tags=["auth"])
def auth_events(limit: int = 100, request: Request = None):
    """Sign-in history. Administrators only."""
    session = require_session(request)
    if session.role != "administrator":
        raise HTTPException(403, "administrator role required")
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT occurred_at, email, event, host(ip) AS ip, detail
                FROM auth_event ORDER BY occurred_at DESC LIMIT %s
            """, (min(limit, 500),))
            return {"items": [dict(r) for r in cur.fetchall()]}


# --- dependency --------------------------------------------------------------

def require_session(request: Request) -> Session:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(401, "Not signed in.")
    session = resolve_session(token)
    if session is None:
        raise HTTPException(401, "Your session has expired. Please sign in again.")
    return session


def session_user(request: Request) -> User:
    """Identity for the rest of the application.

    Falls back to the development headers only when AM_FORECAST_DEV_AUTH=1,
    which must never be set in production. With it unset, a valid session is the
    only way in.
    """
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = resolve_session(token)
        if session:
            return User(username=session.email, role=session.role)
    if DEV_AUTH:
        role = (request.headers.get("X-Role") or "viewer").lower()
        if role in ROLES:
            return User(username=request.headers.get("X-User") or "dev", role=role)
    raise HTTPException(401, "Not signed in.")
