# Runbook

**This file supersedes DEPLOY.md, VERSION_MISMATCH.md, CATCHUP.md,
INVENTORY_FIRST.md and PLAN.md. Those have been withdrawn — parts of each were
overtaken by later findings. Follow this one only.**

---

## Where this deployment actually is

| | State |
|---|---|
| Migrations | cleanly at **0015**. No 0016, 0017 or 0018. |
| `sales_transaction` | **empty** — batch #2 was uploaded 5 August and left `pending`, never accepted |
| `forecast_policy` | 5,837 rows, on the **gross** income basis |
| Cut-off | `2025-12-31`, set by `pytest-admin`. Test residue. |
| Application code | mixed — nine files at 0017 level dropped onto a 0015 codebase |

**Renewals upload and rollback are broken right now.** `app/importers/commit.py`
references `forecast_month_lock`, which migration 0017 creates and this database
does not have. That is my error and the rebuild below fixes it.

---

## Read this before choosing a path

There are two ways forward and the shorter one is now clearly better.

**Path A — rebuild (recommended).** Nothing in this database is irreplaceable.
Sales were never accepted. Renewals are re-importable from a file we hold. July
is being supplied by hand anyway. Users and settings are recreatable in a
minute. Rebuilding at 0018 means **no gross-basis row ever exists**, so the
entire backfill-and-rebase problem simply does not arise.

**Path B — migrate in place.** Apply 0016, backfill the associate columns,
rebase the stored budget baseline, then 0017 and 0018. Every step is documented
and tested, but each is irreversible and the rebase cannot recover rows whose
policy linkage is missing. It preserves audit history and users. That is the
only thing it buys.

The rest of this file is Path A. Path B is in `CHANGES.md` if it is wanted;
`preflight_0016.sh` and `backfill_0016_renewals.sql` support it.

---

## File manifest

Paths relative to the repository root.

### Migrations — `migrations/versions/`
| File | Note |
|---|---|
| `0016_primary_associate_income.sql` | associate income basis |
| `0017_forecast_month_locks.sql` | month locks |
| `0018_two_ledger_model.sql` | two-ledger model, Melbourne month boundary |

### Application code — replace
| File | Destination |
|---|---|
| `commit.py` | `app/importers/commit.py` |
| `engine.py` | `app/matching/engine.py` |
| `validation.py` | `app/validation.py` |

### Tests — replace `tests/`
`conftest.py`, `test_stage1.py`, `test_stage2_import.py`,
`test_stage3_forecast.py`, `test_stage4_matching.py`, `test_stage5_dashboard.py`,
`test_stage6_longevity.py`, `test_stage8_bonus.py`, `test_stage9_base_state.py`

### Scripts — `scripts/`
| File | Purpose |
|---|---|
| `rebuild.sh` | rebuild from empty; finds source files by content |
| `check_state.sh` | damage checks and book shape |
| `inventory.sh` | what is actually in the database |
| `establish_july_2026_baseline.sql` | July 2026 from supplied figures |
| `reconcile_month_baseline.sql` | report-only reconciliation for a month |
| `preflight_0016.sh`, `backfill_0016_renewals.sql` | Path B only |
| `derive_cutoff.sh` | legacy cut-off only; not used by the new model |

```bash
chmod +x scripts/*.sh
```

### Data files
Put in `fixtures/` alongside the existing files:

- **`McMc_Partners_20260714_Renewals_Pending_Summary.csv`** — required. This is
  the August 2026 source.
- A **current sales export** if one can be pulled. See "what will be missing".

---

## Steps

### 1. Back up

```bash
pg_dump "$DATABASE_URL" > backup_$(date +%Y%m%d_%H%M).sql
ls -la backup_*.sql
```

Confirm the file exists and is not trivially small. Do not continue otherwise.

### 2. Rebuild

```bash
CSV_DIR=fixtures bash scripts/rebuild.sh
```

Drops and recreates the schema, runs 0001 to 0018, seeds, imports the sales file
and the **newest** renewals extract, creates users, then runs the state check.

It deliberately loads only the newest renewals extract. Loading older ones as
well is wrong — a newer snapshot supersedes an open month, so bulk-loading the
history ends with only the last file's months present. Tested: it wiped July.
Earlier months are established deliberately in step 3.

**It also establishes only months that began AFTER the extract was pulled.** A
pending report is already missing whatever renewed in the month it was pulled in,
so establishing that month from it sets a target short by however much of the
month had gone — and the current-month freeze then locks that in permanently. The
pull date is inferred from the earliest expiry in the file. Months it withholds
are named in the output, and are established deliberately instead.

This is why running steps 1-3 without the 14 July file is safe but incomplete:
August is withheld rather than set wrongly. It will read `missing_forecast` until
the 14 July file is loaded and step 4 is run.

Expect all three damage checks to read `0`.

### 3. Establish July 2026

```bash
psql "$DATABASE_URL" -f scripts/establish_july_2026_baseline.sql
```

July cannot be derived from any file we hold — the 14 July extract carries only
211 July policies at $182,416.57 against a real $331,676.03, because a pending
report loses whatever has already renewed. The figures were supplied directly
and are written at manager grain, marked `associate` basis, locked and audited.

Expect **13 managers, forecast $331,676.03, target $356,551.73**.

The script aborts without writing if any manager name fails to resolve.

### 4. Verify August 2026

```bash
psql "$DATABASE_URL" -v month="'2026-08-01'" -f scripts/reconcile_month_baseline.sql
```

Report only. Expect **14 managers, forecast $291,970.36, target $313,868.14**
from the 14 July extract, and section 6 confirming a usable source exists.

If it says STOP, the 14 July file is not in `fixtures/`. Do not proceed.

### 5. Confirm

```bash
bash scripts/check_state.sh
AM_FORECAST_FIXTURES=fixtures pytest tests/ --dsn "$DATABASE_URL"
```

Zero failures. Do not expect a particular pass count — this book is larger than
the reference sample, so pass and skip counts will differ.

**If a test fails, report it with the traceback. Do not edit the test to make it
pass and do not edit the production code.** These were rewritten against current
business rules; a failure here is a finding.

---

## What will still be missing, and why that is correct

July and August will show expected income and **no actuals**, because the only
sales file covers FY2025-26 and the forecast covers FY2026-27. They do not
overlap.

They will read `actuals_not_loaded` and `in_progress`, **not 0%**. That
distinction is deliberate: a month nobody has uploaded is not a month everybody
failed. To show real performance, a sales export covering FY2026-27 is needed.

---

## Not yet built — do not assume these work

1. **Forecast upload enforcement.** The lock *logic* is correct — July and
   August closed, September open. The *upload path does not consult it yet*.
   Uploading a forecast Excel today will still overwrite August.
2. **Audited admin override endpoint.** The table and functions exist in 0018;
   the API does not.
3. **API and view cut-over.** The application still reads the old cut-off views.
   0018's views sit alongside them.

Until 1 is built, treat every forecast upload as capable of overwriting a locked
month, and check `v_month_performance` afterwards.

---

## The one operating rule

**Pull the renewals export in the last days of each month, before the next one
begins.**

A pending report lists only what has not yet transacted. The 14 July file gave a
complete August because nothing in August had transacted yet. The same file
pulled on 14 August would have been missing a fortnight of renewals — and once a
month starts, that is locked in.

July is the evidence: 211 policies at $182,416.57 in the mid-month extract,
against $331,676.03 actual. Nearly half the month gone.
