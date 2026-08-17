-- Convert any binary auth columns to text.
--
-- Only relevant to databases that ran an earlier build of 0014, where the hash
-- and salt were bytea. Fresh installs skip every branch: 0014 now creates these
-- as text and this file finds nothing to do.
--
-- The reason for the change is deployment, not cryptography. A managed publish
-- pipeline can refuse an ALTER COLUMN ... TYPE bytea as possibly not backwards
-- compatible, which blocks the whole release. base64 in a text column is the
-- same 32 bytes of scrypt output.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'app_user' AND column_name = 'password_hash'
                 AND data_type = 'bytea') THEN
        ALTER TABLE app_user
            ALTER COLUMN password_hash TYPE text
            USING CASE WHEN password_hash IS NULL THEN NULL
                       ELSE encode(password_hash, 'base64') END;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'app_user' AND column_name = 'password_salt'
                 AND data_type = 'bytea') THEN
        ALTER TABLE app_user
            ALTER COLUMN password_salt TYPE varchar(32)
            USING CASE WHEN password_salt IS NULL THEN NULL
                       ELSE encode(password_salt, 'base64') END;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_name = 'user_session' AND column_name = 'token_hash'
                 AND data_type = 'bytea') THEN
        -- Sessions cannot be re-encoded meaningfully, and a forced sign-in is a
        -- reasonable price for a schema change. Clear them.
        DELETE FROM user_session;
        ALTER TABLE user_session
            ALTER COLUMN token_hash TYPE varchar(64) USING encode(token_hash, 'hex');
    END IF;
END $$;

-- Record which migration files have been applied, so the application can bring
-- a database up to date on its own where a deployment pipeline will not.
CREATE TABLE IF NOT EXISTS schema_migration (
    filename    varchar(200) PRIMARY KEY,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  varchar(120)
);
