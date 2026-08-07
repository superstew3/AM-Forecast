-- Authentication.
--
-- Replaces the header-based identity used during development, where any caller
-- could claim the administrator role simply by sending a header. That was fine
-- while one person was evaluating the app on a laptop and is not fine now.
--
-- Design notes worth keeping with the schema:
--
--   * Passwords are stored as scrypt hashes with a per-user random salt.
--     scrypt is memory-hard, so a stolen database cannot be attacked with
--     cheap parallel hardware the way a plain SHA hash can. The cost
--     parameters are stored per row, so they can be raised over time without
--     invalidating existing passwords.
--   * Session tokens are never stored. Only their SHA-256 hash is kept, so a
--     database leak does not hand over live sessions.
--   * Failed attempts are counted and the account locks temporarily. Lockout
--     is per account rather than per IP because the threat here is guessing a
--     known colleague's password, not a botnet.
--   * Every authentication event is recorded, successful or not.

-- The original column was varchar, left over from a placeholder. A scrypt
-- digest is binary; storing it as text invites an encoding bug that would only
-- surface as a login that never succeeds.
ALTER TABLE app_user
    ALTER COLUMN password_hash TYPE bytea
    USING CASE WHEN password_hash IS NULL THEN NULL
               ELSE convert_to(password_hash, 'UTF8') END;

ALTER TABLE app_user
    ADD COLUMN IF NOT EXISTS email             varchar(255),
    ADD COLUMN IF NOT EXISTS password_salt     bytea,
    ADD COLUMN IF NOT EXISTS password_algo     varchar(20)  NOT NULL DEFAULT 'scrypt',
    ADD COLUMN IF NOT EXISTS password_n        integer      NOT NULL DEFAULT 32768,
    ADD COLUMN IF NOT EXISTS password_r        integer      NOT NULL DEFAULT 8,
    ADD COLUMN IF NOT EXISTS password_p        integer      NOT NULL DEFAULT 1,
    ADD COLUMN IF NOT EXISTS password_set_at   timestamptz,
    ADD COLUMN IF NOT EXISTS must_change_password boolean   NOT NULL DEFAULT false,
    ADD COLUMN IF NOT EXISTS failed_attempts   integer      NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS locked_until      timestamptz,
    ADD COLUMN IF NOT EXISTS last_login_at     timestamptz,
    ADD COLUMN IF NOT EXISTS last_login_ip     inet,
    -- Where a login belongs to an account manager, this ties them together so
    -- the interface can default to their own page.
    ADD COLUMN IF NOT EXISTS canonical_manager varchar(120),
    ADD COLUMN IF NOT EXISTS created_by        varchar(120),
    ADD COLUMN IF NOT EXISTS updated_at        timestamptz NOT NULL DEFAULT now();

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_user_email
    ON app_user (lower(email)) WHERE email IS NOT NULL;

-- Sessions. The token itself is never stored.
CREATE TABLE IF NOT EXISTS user_session (
    id            bigserial PRIMARY KEY,
    user_id       integer NOT NULL REFERENCES app_user(id) ON DELETE CASCADE,
    token_hash    bytea NOT NULL,
    issued_at     timestamptz NOT NULL DEFAULT now(),
    expires_at    timestamptz NOT NULL,
    last_seen_at  timestamptz NOT NULL DEFAULT now(),
    ip            inet,
    user_agent    text,
    revoked_at    timestamptz,
    revoked_by    varchar(120),
    revoke_reason text
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_user_session_token ON user_session (token_hash);
CREATE INDEX IF NOT EXISTS ix_user_session_live ON user_session (user_id)
    WHERE revoked_at IS NULL;

-- Every authentication event, successful or not.
CREATE TABLE IF NOT EXISTS auth_event (
    id         bigserial PRIMARY KEY,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    email      varchar(255),
    user_id    integer REFERENCES app_user(id) ON DELETE SET NULL,
    event      varchar(40) NOT NULL CHECK (event IN (
        'login_success', 'login_failed_password', 'login_failed_unknown_user',
        'login_failed_inactive', 'login_failed_locked', 'account_locked',
        'logout', 'password_changed', 'password_reset_by_admin',
        'session_expired', 'session_revoked', 'user_created', 'user_disabled')),
    ip         inet,
    user_agent text,
    detail     jsonb
);

CREATE INDEX IF NOT EXISTS ix_auth_event_time ON auth_event (occurred_at DESC);
CREATE INDEX IF NOT EXISTS ix_auth_event_email ON auth_event (lower(email));

-- Backfill the username column for accounts created by email.
UPDATE app_user SET email = username
WHERE email IS NULL AND username LIKE '%@%';
