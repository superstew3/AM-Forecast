---
name: am_forecast pytest suite mutates a persistent dev DB
description: Repeated pytest runs against the dev database can accumulate real, committed state and produce flaky/order-dependent failures unrelated to the code under test.
---

The am_forecast test suite (`am_forecast/tests/`) drives the FastAPI app's own
HTTP endpoints for many tests. Those endpoints commit through the app's own
DB connection, not the test's `conn` fixture — so the autouse
`_clean_transaction` rollback in `conftest.py` does NOT undo them. Anything a
test writes via the API (e.g. growth-rate overrides, budget locks) persists
in the dev database across separate `pytest` invocations.

**Why:** After running the full suite several times in one session (e.g. to
re-check after a migration), growth_rate rows and similar app-mutated state
accumulate for real. This produced 4 failing tests that were reproducible in
isolation but had nothing to do with the migration under test — the actual
cause was leftover rows from earlier runs shadowing new writes via the app's
"most specific scope wins" precedence rule.

**How to apply:** If the full suite shows unexplained failures unrelated to
the files just changed — especially failures whose expected/actual values
look like a stale, previously-set value — suspect accumulated real state
before assuming a code regression. Rebuild the dev database from fixtures
(`CSV_DIR=fixtures bash am_forecast/scripts/rebuild.sh`) to get a clean
baseline, then re-run the suite once. Note that `rebuild.sh` also
re-generates the three seed-user accounts with random passwords (see
[am-forecast-user-seeding.md](am-forecast-user-seeding.md)) — reset them
from the `AM_FORECAST_PW_*` secrets afterward if the app needs to stay
usable with known credentials.
