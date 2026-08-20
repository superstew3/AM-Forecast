---
name: Dual user-creation paths in am_forecast
description: Two different code paths create the three seed-user accounts with different passwords; running the wrong one after the right one breaks login silently.
---

`app/bootstrap.py`'s `seed_users()` hashes `AM_FORECAST_PW_MICHAEL` /
`_SAM` / `_ANASTASIA` and inserts the three accounts with
`ON CONFLICT DO NOTHING` — it only ever creates rows, it never updates an
existing one. `scripts/rebuild.sh` separately calls its own
`create_users.py`, which generates **random** passwords for the same three
accounts (unrelated to those secrets).

**Why:** If `rebuild.sh` runs after the accounts already exist with
secret-derived passwords (e.g. during a dev-DB rebuild done to clear
unrelated test pollution), it silently replaces their hashes with random
ones. Re-running `python -m app.bootstrap` afterward looks like it should
fix login but does nothing, because the accounts still exist and
`ON CONFLICT DO NOTHING` skips them — the password rows are never repaired.
This produced a real "my email and password isn't working" report after an
otherwise-unrelated rebuild.

**How to apply:** Do not trust `python -m app.bootstrap` to repair
passwords for existing accounts — it cannot. If login fails after any
`rebuild.sh` run, reset passwords directly with an `UPDATE app_user SET
password_hash=..., password_salt=..., password_n=..., password_r=...,
password_p=..., password_set_at=now(), must_change_password=false WHERE
lower(email)=lower(...)`, hashing each `AM_FORECAST_PW_*` secret with
`app.api.auth.hash_password()` (scrypt, base64-encoded output — see that
module for the exact tuple shape). Check `app_user.password_set_at` for a
timestamp matching a recent `rebuild.sh` run as the tell.
