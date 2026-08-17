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
| `service.py` | `app/importers/service.py` |
| `engine.py` | `app/matching/engine.py` |
| `validation.py` | `app/validation.py` |

`service.py` parses an upload; `commit.py` writes it. They are one change split
across two files and **must be replaced together** — `commit.py` expects
`primary_assoc_comm_sum` in the parsed policy, and only the matching `service.py`
puts it there. Shipping one without the other fails at the renewals accept with
`KeyError: 'primary_assoc_comm_sum'`.

| `main.py` | `app/api/main.py` |

I previously said `main.py` did not need replacing. That was wrong. I checked
whether it referenced 0016-era *schema*, not whether its *imports* matched the
`validation.py` being shipped. This repl's `main.py` imports `BASE_POSITION`,
which was renamed to `INCOME_BASIS` — so the application cannot import at all
until both move together.

**On the auth-seeding fix:** the version supplied already wires bootstrap in, via
`from ..bootstrap import run` behind `AM_FORECAST_AUTO_MIGRATE`. Keep this repl's
`app/bootstrap.py` — do not replace it. If anything auth-related then fails,
report it rather than patching; that would mean the fix touched `main.py` itself
and needs merging deliberately.

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

### 4. Establish August 2026 from the 14 July extract

`rebuild.sh` does **not** pick this file. It selects the most recent extract by
inferred pull date, and `Renewals_Pending_Summary_-_now-june2027.csv` is more
recent than the 14 July one — so the automatic choice is correct by its own rule
and wrong by yours. August is a deliberate decision, not a heuristic:

```bash
python3 scripts/set_month_forecast_from_file.py "$DATABASE_URL" \
    fixtures/McMc_Partners_20260714_Renewals_Pending_Summary.csv \
    --month=2026-08-01 \
    --reason="14 July extract: last file pulled before August began"
```

Expect **$291,970.36 across 14 managers**, and the month pinned against later
snapshots.

Then confirm, report only:

```bash
psql "$DATABASE_URL" -v month="'2026-08-01'" -f scripts/reconcile_month_baseline.sql
```

September 2026 onward keeps whatever `rebuild.sh` loaded from the newer extract,
which is correct — those months had not started when either file was pulled.

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
