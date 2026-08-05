# Account Manager Income Forecasting Platform

Stages 1-5: database, reference data, import service, forecast versioning,
budget engine, forecast-to-actual matching, API and dashboard.

> **Current operating position** is the base supplied snapshot only. Every figure
> in the "Current operating position" sections below comes from the two supplied
> source files and the legacy July baseline. Figures labelled **synthetic** come
> from a generated test fixture and are never part of the reported position; the
> fixture is rolled back after every use and `tests/test_stage9_base_state.py`
> asserts that the database is returned to the base state.

**All income figures are GST inclusive.**

---

## What Stage 1 delivers

| Component | Location | Status |
|---|---|---|
| Data model, 23 tables | `app/models/` | Applied and verified on PostgreSQL 16 |
| Initial schema DDL | `migrations/versions/0001_initial_schema.sql` | Generated from models |
| Reporting views and functions | `migrations/versions/0002_reporting_views.sql` | Applied |
| Reference data seed | `app/seed/` | 15 managers, 22 aliases, 15 exclusion rules, 10 categories, 24 baselines |
| Legacy forecast extractor | `scripts/extract_legacy_forecast.py` | Run against the workbook |
| Staging, sighting, rollback tables | `migrations/versions/0003_import_staging.sql` | Applied |
| Import service | `app/importers/` | All three source files imported through it |
| Import CLI | `scripts/import_cli.py` | detect / prepare / accept / reject / rollback |
| Forecast movement engine | `app/forecast/movement.py` | Wired into accept |
| Snapshot coverage rules | `app/forecast/coverage.py` | Gates mass removal |
| Matching engine | `app/matching/engine.py` | Tiered, with review queue |
| Class equivalence seed | `app/seed/class_equivalence.py` | 96 mappings, 99% volume |
| Stage 3-4 views | `migrations/versions/0004`, `0006`, `0008` | Applied |
| Match reporting | `scripts/match_report.py` | All Stage 4 outputs |
| Synthetic match fixture | `scripts/make_match_fixture.py` | Test only, always rolled back |
| API | `app/api/` | FastAPI, typed models, RBAC, exports |
| Dashboard | `web/` | React 18 + TypeScript + Vite |
| Static preview | `scripts/build_preview.py` | Renders live API data to HTML |
| Acceptance tests | `tests/`, `web/src/__tests__` | 137 + 11 passing |

The Stage 1 validation harness (`scripts/load_sources.py`) has been removed. The
import service replaced it; keeping both would have meant two ingest code paths
that could drift apart and disagree about a total.

The models are the single source of truth. `scripts/generate_ddl.py` compiles them
to PostgreSQL DDL, so the migration cannot drift from the models.

---

## Setup

```bash
pip install -r requirements.txt
createdb am_forecast

psql -d am_forecast -f migrations/versions/0001_initial_schema.sql
psql -d am_forecast -f migrations/versions/0002_reporting_views.sql

python -m app.seed.load_seed "dbname=am_forecast"

python scripts/extract_legacy_forecast.py \
    Account_Manager_Sales_Dashboard_FY2025-27.xlsx legacy_forecast.csv

# Import is two-phase: prepare stages and previews, accept commits.
DSN="dbname=am_forecast"
python scripts/import_cli.py "$DSN" prepare Sales_Transaction_List_25-26.csv --user=sam
python scripts/import_cli.py "$DSN" accept 1 --user=sam

python scripts/import_cli.py "$DSN" prepare Renewals_Pending_Summary_-_now-june2027.csv --user=sam
python scripts/import_cli.py "$DSN" accept 2 --user=sam

python scripts/import_cli.py "$DSN" prepare legacy_forecast.csv --user=sam
python scripts/import_cli.py "$DSN" accept 3 --user=sam

# After all three imports are accepted, run the matcher before the test suite.
python scripts/match_report.py "$DSN" --run --user=sam

pytest tests/ --dsn "$DSN"
```

## The import workflow

`prepare` detects the report type, parses and validates every row, applies
manager aliases and exclusion rules, computes financial values, and writes the
result to `import_staging`. It touches no fact table. The preview it returns is
computed from the staged rows, so the figures a user approves are exactly the
figures that will land.

`accept` promotes the staged rows verbatim and clears staging. It re-derives
nothing.

`reject` discards staging and marks the batch rejected.

`rollback` reverses an accepted batch.

Example preview, before anything is committed:

```
Batch 1: Sales_Transaction_List_25-26.csv
  Detected: Sales Transaction Report (confidence 100%)
  Source rows                14,886
  Valid                      14,886
  Highview excluded           2,163
  Duplicates                      0
  Restated                        0
  Rejected                        0
  Positive income          5,620,647.70
  Return income             -659,271.01
  Net income               4,961,376.69
  Coverage               2025-05-01 to 2026-07-31
  Exceptions                      0
  ! 'SpecialFees' is retained for reference only (component of Fees; adding it double counts).
  ! 'Fee' is retained for reference only (component of Fees; adding it double counts).
  All income figures are GST inclusive.
```

Re-running the same file previews as 14,886 duplicates, zero new income, and a
warning that the file is byte-identical to an already-accepted batch. The user
sees that before committing, not after.

### Detection

Report type is scored against known column signatures rather than taken from the
filename. Filenames here are not reliable: the supplied
`Sales_Transaction_List_25-26.csv` actually spans May 2025 to July 2026, across
three financial years. A column mapping can be supplied or saved as a profile
when an insurer renames a field, so a renamed column is an administrator task
rather than a code change.

Detection also names the fields that must never be summed —`SpecialFees` and
`Fee` on sales, `Admin`, `AdminTax`, `Special` and `SpecialTax` on renewals — so
the double-counting trap is visible at the point of import.

### What blocks an accept

An unresolved `error`-severity exception blocks it. Currently that means an
unmapped category or a source manager with no alias row. Both would otherwise
produce income that belongs to no one. `force=True` overrides deliberately and
is recorded.

Warnings do not block: negative expected income, overdue pending renewals and
restated transactions are surfaced but left to judgement.

### Rollback

Reversing a cumulative sales report is only deterministic because every sighting
is recorded in `transaction_sighting`. A transaction seen in batches 1, 2 and 3
must survive rollback of batch 3 with its `last_seen` restored to batch 2, not
left pointing at a batch that no longer exists.

Verified against a 700-row incremental file containing 500 rows already loaded
and 200 new ones:

| | Result |
|---|---|
| Transactions deleted | 200 |
| Sightings removed | 700 |
| Shared rows repaired | 500 |
| Net income after rollback | $4,961,376.69, restored exactly |
| Rows still pointing at the rolled-back batch | 0 |

Rolling back a snapshot is blocked when a newer accepted snapshot exists, since
that would strand the Original Forecast for its months. The block explains what
to do instead and can be forced by an administrator.

A restated transaction — a fingerprint that reappears with different supporting
values such as a changed premium or policy class — is never silently
overwritten. It goes to `restated_transaction` for review.

---

## Verified reconciliation

Loaded through the real schema, not a spreadsheet check.

| Measure | Confirmed | Loaded |
|---|---|---|
| Sales source rows | 14,886 | 14,886 |
| Sales Highview excluded | 2,163 | 2,163 |
| Sales included | 12,723 | 12,723 |
| Positive Actual Income | $5,620,647.70 | $5,620,647.70 |
| Return Income | ($659,271.01) | ($659,271.01) |
| Net Actual Income | $4,961,376.69 | $4,961,376.69 |
| Renewals source rows | 6,749 | 6,749 |
| Unique PolicyIDs | 6,749 | 6,749 |
| Renewals excluded | 975 | 975 |
| Renewals included | 5,774 | 5,774 |
| Negative expected rows | 3 | 3 |
| Zero expected rows | 11 | **12** — see below |
| Raw expected income | $3,352,917.06 | $3,352,917.06 |
| Forecast contribution | $3,354,995.38 | $3,354,995.38 |

### The zero-row count is 12, not 11

PolicyID 931173620 (ALEXSSH, CHCPOL10211306, Home Insurance, expiry 31 May 2027)
carries Comm 206.73 and CommTax 20.68 fully offset by Fee -206.73 and FeeTax
-20.68. In exact decimal arithmetic that is precisely zero. A floating-point
pipeline returns 7.1e-15 and counts the row as non-zero, which is where the
figure of 11 came from.

Every monetary total is unaffected. The row is a fee exactly cancelling
commission, which is worth a look in its own right, and it is visible in the
zero-expected exception list.

This is the reason money is `numeric(14,2)` everywhere and no float touches the
financial path.

---

## Decisions implemented

### July 2026 Original Forecast

Not backfilled from actual income. Deriving the baseline from the result would
collapse the comparison the baseline exists to make. The vocabulary retains
`derived_from_actuals` as a capability, and a test asserts no row uses it.

The workbook's `Forecast Data` sheet holds a traceable July 2026 series, so that
is the baseline, labelled **Legacy Dashboard Forecast**, stored at
`manager_month` grain because no policy detail exists behind it.

| July 2026 | Amount |
|---|---|
| Legacy series total | $348,259.82 |
| Adopted as Original Forecast | **$348,149.67** across 17 managers |
| Withheld | Cameron Stewart $110.15 |

Cameron Stewart is withheld because the legacy workbook applies no Highview
exclusion, and his legacy series totals roughly $57k against a non-Highview book
worth about $600 a year of actual income. Those values cannot be shown to be
Highview-free, so they are not trusted as a baseline. Impact is 0.03% of the
month.

Managers reporting **N/A** rather than 0% for July 2026: Cameron Stewart
(unverifiable), Dinghy Scheme and Anastasia K (no legacy row).

The two remaining July policies in the pending file stay in the Latest Forecast,
flagged `residual_pending`. One is also `overdue_pending`. Neither contributes to
the Original Forecast.

Resulting FY2026-27 Original Renewal Forecast by quarter:

| Quarter | Original Forecast |
|---|---|
| Q1 Jul-Sep | $962,824.04 |
| Q2 Oct-Dec | $805,442.96 |
| Q3 Jan-Mar | $831,586.94 |
| Q4 Apr-Jun | $1,102,038.66 |
| **FY total** | **$3,701,892.60** |

Q1 is flagged as having a mixed baseline: July is manager-month grain from the
legacy dashboard, August and September are policy grain from the snapshot.
Policy-level renewal achievement is reliable from August 2026 onward.

### Achievement is NULL, never zero

Enforced in the database rather than the application, so no report can bypass it.

- `forecast_baseline` declares each month `complete`, `incomplete` or `unavailable`,
  plus a `manager_exceptions` list so one untrustworthy manager line does not
  invalidate a whole month.
- `safe_div()` returns NULL on a zero or null denominator.
- `v_renewal_performance_month` and `v_budget_performance_quarter` return NULL for
  variance and achievement wherever the baseline is not usable.
- FY2025-26 months before November 2025 are `unavailable`: the legacy forecast
  series begins in November, so July to October 2025 report N/A, not zero.

### Budget

`Total Budget = Original Renewal Forecast + New Business Growth Target`, with the
growth target as a percentage of the Original Renewal Forecast. The rate is not
tuned to reproduce the old workbook's total.

Default 7.5%, resolved most-specific-first by `resolve_growth()`:
manager and quarter, then manager, then global. A direct dollar override at any
level supersedes the percentage there, and the active basis is reported alongside
every figure.

FY2026-27 at the 7.5% default:

| | Amount |
|---|---|
| Original Renewal Forecast | $3,701,892.60 |
| New Business Growth Target | $277,641.95 |
| **Total Budget** | **$3,979,534.55** |

Prior-year actual income is exposed separately through `v_prior_year_comparison`
so management can see that the new method deliberately produces a different
result. Prior-year endorsements, cancellations and new business are never blended
into the renewal forecast.

### Anastasia K

Not mapped to Michael Stewart. `MMSTEWART` appearing as primary associate is not
sufficient grounds. Held as `legacy_unmapped`:

- actual income counts towards business totals ($5,773.48 across the file);
- no forecast, no budget, no budget rows generated;
- achievement N/A, never 0%;
- excluded from rankings by default;
- visible in the unmapped/legacy review area for an administrator to map later.

`include_in_rankings` and `include_in_business_totals` are separate flags because
they answer different questions.

### Period labelling

`period_coverage` records what the data actually covers, so a fragment is never
read as a year.

| FY | Domain | Status | Months |
|---|---|---|---|
| 2024-25 | actuals | **partial** | 2 (May-Jun 2025) |
| 2025-26 | actuals | complete | 12 |
| 2026-27 | actuals | partial | 1 (Jul 2026, in progress) |
| 2025-26 | forecast | **partial** | 8 (Nov 2025-Jun 2026) |
| 2026-27 | forecast | complete | 12 |

### Matching

No matching engine is enabled in Stage 1. `forecast_actual_match` and the
snapshot history are in place, and `legacy_forecast_reference` retains the full
216-row legacy series including the months not promoted. A historical Renewals
Pending snapshot can be uploaded later and backtested without schema change.

Note that the November 2025 forecast is partly recoverable: the workbook's
`Forecast Data` sheet covers November 2025 to October 2026 at manager-month
grain. That supports monthly renewal achievement backtesting but not
policy-level match accuracy, which needs policy detail.

---

## Design notes

**Canonical manager is never stored on a fact row.** It is resolved by join
through `v_manager_resolution`, so correcting an alias retrospectively fixes
actuals, forecasts and budgets together.

**Excluded records are flagged, never dropped.** Every Highview row is imported
in full with its matching rule, field and value, and omitted from totals by the
reporting views.

**The transaction fingerprint is validated.** SHA-256 over invoice number,
timestamp, client code, policy number, category, commission, fees and source
manager gives 14,886 distinct values for 14,886 rows, zero collisions. Re-upload
increments `seen_count` and changes no total. Verified by test.

**Exception flags are an array.** A policy can be zero-expected and residual at
once, or overdue and residual. A single-valued column silently loses one, which
is how the July flagging defect surfaced during testing.

**Generated columns carry the money arithmetic.** `actual_income`,
`positive_income`, `signed_return_income`, `absolute_return_income`,
`raw_expected_income` and `forecast_contribution` are computed by PostgreSQL, so
no caller can produce a different answer. `forecast_contribution` is
`GREATEST(raw, 0)`, which is why no monthly forecast can go negative.

---

---

## Forecast versioning and movement

Accepting a snapshot records the movement it creates, policy by policy, against
the previous snapshot. Movement is computed once at accept time and stored, so
the Original-to-Latest position never gets derived on the fly and never
disagrees with itself between reports.

| Movement type | Latest effect | Original effect |
|---|---|---|
| `unchanged` | replace | none |
| `amount_changed` | replace | none |
| `manager_changed` | replace | none |
| `detail_changed` | replace | none |
| `removed_from_latest` | **remove, never negate** | retained |
| `added_after_original` | add, original = 0 | none |

Verified against a revised snapshot built to exercise every path:

| | Policies | Movement |
|---|---|---|
| Removed from Latest | 400 | ($85,555.52) |
| Amount changed | 80 | $2,655.84 |
| Added after Original | 60 | $13,971.72 |
| Manager transferred | 40 | $0.00 |
| Unchanged | 5,252 | $0.00 |
| **Net** | | **($68,927.96)** |

That net equals the difference between the two snapshots' contributions to the
cent, which is the reconciliation test.

Two rules are enforced structurally rather than by convention:

**The Original Forecast is guarded against `original_forecast` itself**, not
against the `forecast_month_coverage` index. Coverage is derived; the baseline
must be protected by the thing that holds it. This matters: an earlier version
checked coverage, and when a rolled-back snapshot cleared coverage rows a
later upload silently wrote 60 new policies into a frozen month.

**The first snapshot records no movement.** It establishes the baseline rather
than moving it. Without that guard every policy in the opening file classifies
as `added_after_original` and the opening position reads as a fictitious gain of
the entire book.

### A completed month has no Latest Forecast

`v_forecast_position_month` returns NULL for Latest and movement on any month at
or before the cut-off, because a completed month reports actuals. Returning zero
would have shown July 2026 with a $348k adverse forecast movement caused by
nothing more than its renewals having already transacted.

---

## Budget

Quarterly budget is unchanged by forecast movement, by design and by test. With
the revised snapshot loaded and Latest down $68,927.96, FY2026-27 Total Budget
stays at $3,979,534.55.

The quarterly growth target is spread across months by each month's share of
that quarter's Original Renewal Forecast, not in equal thirds. FY2026-27 Q2
shows why:

| Month | Original Forecast | Weighted target | Equal split would give |
|---|---|---|---|
| Oct 2026 | $283,846.60 | $21,288.49 | $20,136.07 |
| Nov 2026 | $381,071.27 | $28,580.35 | $20,136.07 |
| Dec 2026 | $140,525.09 | $10,539.38 | $20,136.07 |

An equal split would over-target December by 91% and under-target November by
30%. At manager level it is starker: Sam Stewart's December renewal book is
$4,032, and an equal split would hand him a $2,657 new business target for the
month against a weighted $302.

Monthly overrides are supported and reported with their reason. A quarter with
no original forecast falls back to an equal split rather than dividing by zero.

---

## Latest Outlook

Completed-period Net Actual Income plus Latest Renewal Forecast for future
periods. No assumed future new business: new business is recognised only when it
appears in Sales Transactions.

FY2026-27, with the revised snapshot loaded:

| | Amount |
|---|---|
| Completed actual (July 2026) | $322,876.08 |
| Future Latest Forecast | $3,284,814.97 |
| **Latest Outlook** | **$3,607,691.05** |
| Total Budget | $3,979,534.55 |
| **Remaining budget gap** | **$371,843.50** |

The gap is the income still to be found through new business, retention or other
actual activity.

---

---

## Matching

### Policy class is a mapping, not a string

The two sources do not share a class vocabulary. Renewals Pending says
`COMMERCIAL MOTOR` and `HOUSEBOAT INS`; Sales Transactions says `COMM MOTOR` and
`HBOAT`. Only 28 of 89 renewals classes match a sales class as a string, so class
agreement is resolved through `class_equivalence`, seeded with 96 mappings
covering **99.0% of renewals policies and 99.2% of renewal transactions by
volume**. The long tail is deliberately left unmapped rather than guessed at; an
administrator adds rows as they surface in the review queue.

An unmapped value on either side is *unknown*, not *incompatible*: it cannot earn
the top tier but never blocks a match on client and policy number.

### Tier order

Most specific evidence first. Class agreement raises a match; it never lowers one.

| Tier | Evidence | Confidence | Auto |
|---|---|---|---|
| 1 | client + policy number + compatible class + date within tolerance | 0.98 | yes |
| 2 | client + policy number + date within tolerance | 0.90 | yes |
| 3 | client + policy number, same financial year | 0.75 | yes |
| 4 | client + compatible class + date within tolerance | 0.55 | **review only** |
| — | several policies competing at the winning tier | — | **review only** |

A class *conflict* — both sides mapped, to different canonical classes —
disqualifies tiers 1 and 4 but demotes a policy-number match only to tier 2.
Policy number is the stronger identifier and is not overridden by a class
disagreement.

New business is not in the matchable set at all, so a policy's originating `N/B`
transaction can never be credited as its renewal.

### Outcome and income are separate questions

Whether a policy renewed is not the same as which dollars count towards renewal
achievement.

| Outcome | Renewal income |
|---|---|
| Renewed / Transfer Renewed | RWL / TRW allocated to the policy |
| Lapsed / Lost | **zero**, always |
| Pending | zero — the renewal window is still open |
| Removed from Latest Forecast | zero |
| Multiple Candidates / Unmatched | zero |
| Manually Resolved | as the reviewer allocated |

Two measures are kept:

- **Renewal Transaction Income** — RWL and TRW only, plus corrections with a
  defensible link. Drives renewal achievement.
- **Total Actual Income Associated with the Policy** — every allocated line.
  Answers "what did this policy generate", which is a different question.

An ordinary `END` or `ECN` on a renewed policy stays endorsement income. An
`ADJ`, `END` or `ECN` counts as renewal income **only** where it shares an
invoice number with a renewal transaction allocated to the same policy — an
invoice chain is the defensible accounting link, not mere proximity.

A lapse produces a Lost Renewal with zero renewal income. Its negative
transaction still reduces Net Actual Income and appears in Return Income; it is
simply never presented as negative renewal income "achieved".

A policy whose expiry falls within the date tolerance of the cut-off is
**Pending**, not Unmatched. Without that rule every period end would manufacture
lapses out of renewals that had not yet been processed.

### One transaction, one credit

Distinct PolicyIDs legitimately share client, policy number and expiry. Three
layers prevent double counting:

1. **Class resolves what it can.** Where twins differ in class and the
   transaction names one, that one is credited and the other gets nothing.
2. **Otherwise nothing is credited.** Competing policies at the winning tier all
   go to the review queue with zero automatic allocation.
3. **The database enforces it.** A partial unique index permits at most one
   automatic allocation per transaction, and a deferred constraint trigger
   rejects any set of allocations that exceeds the source transaction's income or
   flips its sign. Apportionment is possible, but only as a deliberate manual act
   with explicit amounts.

`v_allocation_breaches` is the standing check and must always be empty.

### Manual review

`manual_match`, `reject_match` and `apportion` each write to `match_decision`
with reviewer, timestamp, reason, the previous decision and the new one.
Re-running the matcher clears automatic allocations only; manual allocations and
their audit survive. Outcomes are recomputed from surviving allocations every run
rather than preserved, so a rolled-back batch can never leave a policy showing
renewal income with nothing behind it.

---

## Snapshot coverage

A month absent from a newer Renewals Pending file is ambiguous: it might mean
every policy has gone, or that the export was narrower, filtered or uploaded out
of order. Treating absence as removal is the dangerous reading, so coverage is
**declared, not assumed**.

The upload preview shows the months covered, the policy count and value in each,
the months the previous snapshot had that this file does not, and how many
policies and how much income would be treated as removed. A month losing more
than half its policies is flagged as a mass removal.

Accept is blocked until the uploader passes `confirmed_months` naming the months
the file covers in full. Only those months are compared, so an unconfirmed month
is left exactly as it was. The preview also refuses to proceed when the Reporting
Cut-Off Date lags the latest actual transaction, since a completed month must not
be compared as though it were still open.

---

## Current operating position (base supplied snapshot)

Reporting cut-off 31 July 2026. One snapshot, no synthetic data.

| | Amount |
|---|---|
| Net Actual Income (all periods in file) | $4,961,376.69 |
| FY2026-27 Original Renewal Forecast | $3,701,892.60 |
| FY2026-27 Total Budget @ 7.5% | $3,979,534.55 |
| FY2026-27 Latest Outlook | $3,676,619.01 |
| **Remaining budget gap** | **$302,915.54** |

### Real matching results, July 2026

This is the whole of what the supplied data can exercise.

| | |
|---|---|
| Forecast policies in scope | 2 |
| Auto matched | 0 |
| Outcome | both **Pending** |
| Unmatched actual renewals (July 2026) | 584, $344,968.66 |
| Renewal transactions outside matching scope | 8,071, $4,447,979.30 |

Both July policies are Contract Works, and neither has a renewal transaction —
only the original new-business line that created them. The matcher correctly
declined to credit an `N/B` as a renewal. One expires 12 July and one on 31 July,
both inside the 45-day tolerance of the cut-off, so both are Pending rather than
Unmatched.

The 584 unmatched July renewals are the snapshot-timing artefact identified in
Phase 0: the pending file was extracted after most July renewals had already
transacted, so there is no forecast policy to match them against. The 8,071
transactions outside scope fall in FY2025-26, which has no policy-grain forecast
at all — they are not match failures, and queuing them would bury the real
exceptions.

**Matching accuracy cannot be measured on this data.** It needs a forecast period
that overlaps transacted actuals. That arrives naturally with the next Renewals
Pending snapshot, or immediately if a historical snapshot covering FY2025-26 is
supplied.

---

## Synthetic matching scenario (test only, never part of the position)

`scripts/make_match_fixture.py` generates transactions against real August 2026
forecast policies to exercise every path. It is imported as a normal batch and
rolled back afterwards.

| Tier | Allocations | Policies | Allocated | Renewal income |
|---|---|---|---|---|
| 1 (0.98) | 52 | 45 | $20,114.14 | $21,541.81 |
| 2 (0.90) | 3 | 3 | $2,296.66 | $2,296.66 |
| 3 (0.75) | 5 | 4 | $1,184.75 | $1,193.93 |

Renewal income exceeds allocated income at tiers 1 and 3 because allocated
includes negative endorsement and lapse lines that are correctly excluded from
renewal income.

| Outcome | Policies | Original forecast | Renewal income | Total associated |
|---|---|---|---|---|
| Pending | 319 | $233,918.87 | $0.00 | $93.95 |
| Renewed | 39 | $22,060.34 | $21,329.36 | $21,778.09 |
| Transfer Renewed | 5 | $3,703.04 | $3,703.04 | $3,703.04 |
| Lapsed / Lost | 5 | $1,979.53 | **$0.00** | ($1,979.53) |
| Multiple Candidates | 3 | $2,149.53 | $0.00 | $0.00 |
| Unmatched | 1 | $0.00 | $0.00 | $0.00 |

Duplicate-allocation control across all 60 allocations: maximum policies credited
per transaction 1, maximum automatic allocations per transaction 1, zero
breaches.

---

---

## Running the application

```bash
# Backend
pip install -r requirements.txt
export AM_FORECAST_DSN="dbname=am_forecast"
uvicorn app.api:app --host 127.0.0.1 --port 8000
# API docs at http://127.0.0.1:8000/docs

# Frontend, development
cd web && npm install && npm run dev      # http://localhost:5173, proxies /api

# Frontend, production
cd web && npm run build                   # the API then serves web/dist itself
```

Tests:

```bash
pytest tests/ --dsn "dbname=am_forecast"   # 137 backend and API tests
cd web && npm test                          # 11 frontend formatting tests
npm run typecheck
```

A static preview rendered from live API responses, for review without Node:

```bash
python scripts/build_preview.py "dbname=am_forecast" dashboard_preview.html
```

Roles are supplied by the `X-User` and `X-Role` headers, so the app can sit
behind whatever authentication the business already uses. Replacing
`current_user` in `app/api/core.py` is the only change needed to move to SSO.

| Role | May |
|---|---|
| Viewer | View every reporting area, drill down, export |
| Manager | The above |
| Administrator | The above, plus upload, accept, reject, roll back, maintain mappings and exclusions, adjust budgets, resolve matching, rebaseline |

Every consequential action writes an audit row: `budget_audit` for budget
changes, `match_decision` for matching decisions, `batch_rollback` for
reversals, and `upload_batch` for every file.

---

## Where the numbers come from

Nothing in the API or the frontend recomputes a financial figure. Every
monetary value is selected from a database view; the API filters, paginates and
serialises, and the frontend formats. If a number is wrong there is exactly one
place to fix it.

The frontend never sums the visible page to produce a total. Totals arrive from
the server, because summing a paginated table understates every total the moment
pagination begins.

### N/A survives the whole way to the screen

An unavailable measure is serialised as `{"value": null, "available": false,
"reason": "..."}` and rendered as **N/A** with the reason in a tooltip. It is
never coerced to `$0`, `0%`, a blank cell or a failed result. Reporting 0%
against a manager whose baseline does not exist would say they failed when the
truth is that we cannot say.

This applies to unavailable Original Forecast periods, incomplete baselines,
managers with no applicable budget, the July 2026 manager exceptions, zero
denominators, completed months with no Latest Forecast, and matching periods
with no policy-grain forecast. Eleven frontend tests pin the distinction down,
including that a real zero still renders as zero.

### Currency rounding

Rounding is half away from zero, defined once in `app/api/core.to_cents`,
because Python's default is half-to-even and disagrees with PostgreSQL and with
accounting convention. Getting this wrong showed the Total Budget as
$3,979,534.54 instead of $3,979,534.55.

Budget derives from a percentage, so it carries sub-cent precision:
$3,701,892.60 x 7.5% = $277,641.945. Rounding at manager-and-quarter grain was
tried and rejected — fifty-six separate roundings accumulate and moved the total
to $3,979,534.57. Full precision is kept through every aggregate so a drill-down
sums exactly to its parent; rounding happens at display, and exports carry the
unrounded values deliberately.

---

## Current operating position

The dashboard defaults to the clean base state and asserts it on every test run.

| | Amount |
|---|---|
| FY2026-27 Original Renewal Forecast | $3,701,892.60 |
| FY2026-27 Total Budget @ 7.5% | $3,979,534.55 |
| FY2026-27 Latest Outlook | $3,676,619.01 |
| **Remaining Budget Gap** | **$302,915.54** |
| Reporting Cut-Off Date | 31 July 2026 |

`GET /api/base-position` returns these alongside the live figures and a per-check
result, so the interface can warn if the database is not in the base state.
Synthetic fixtures are generated on demand, rolled back after use, and never
reachable from a production endpoint.

---

## Zero expected-income policies: 12

The confirmed figure is **12**, not the 11 in the original brief. PolicyID
931173620 carries Comm 206.73 and CommTax 20.68 exactly offset by Fee -206.73
and FeeTax -20.68. In exact decimal arithmetic that is precisely zero; a
floating-point pipeline returns 7.1e-15 and counts the row as non-zero.

No financial total changes. The figure is defined once in `app/validation.py`
and flows from there to the reconciliation tests, the upload preview exception
counts, the data-quality indicator and its drill-down, and this document. Zero
expected-income rows are raised as an `info` exception at upload so they appear
in the preview rather than only as a silent flag.

---

## Dashboard acceptance tests

All twenty pass, in `tests/test_stage5_dashboard.py`.

| # | Check | Test |
|---|---|---|
| 1 | The four base operating-position figures reconcile exactly | `test_01_base_operating_position_reconciles` |
| 2 | Synthetic data is absent after tests complete | `test_02_no_synthetic_data_present`, `test_stage9_base_state.py` |
| 3 | Dashboard totals equal database-view totals | `test_03_dashboard_totals_equal_view_totals` |
| 4 | Positive Actual plus signed Return Income equals Net Actual | `test_04_positive_plus_return_equals_net` |
| 5 | N/A is not displayed as zero | `test_05_unavailable_measures_are_null_not_zero`, `test_05b_july_manager_exceptions_report_na`, plus 11 frontend tests |
| 6 | Completed months show no Latest Forecast | `test_06_completed_month_has_no_latest_forecast` |
| 7 | July 2026 displays the legacy-baseline warning | `test_07_july_legacy_baseline_is_declared` |
| 8 | The 12 zero-income pending policies appear in Data Quality | `test_08_twelve_zero_income_policies_in_data_quality` |
| 9 | Highview records do not appear in reported totals | `test_09_highview_absent_from_reported_totals` |
| 10 | Highview records remain in the excluded-data audit view | `test_10_highview_remains_in_the_excluded_audit_view` |
| 11 | Anastasia K counts in business totals, not in rankings | `test_11_anastasia_in_totals_but_not_rankings` |
| 12 | Manager transfers counted by the independent flag | `test_12_manager_transfers_counted_by_independent_flag` |
| 13 | One transaction cannot be credited to several policies | `test_13_no_transaction_credited_to_several_policies` |
| 14 | Renewal income is RWL/TRW plus linked corrections only | `test_14_renewal_income_is_rwl_trw_plus_linked_corrections` |
| 15 | Lapse reduces Net Actual but yields zero renewal income | `test_15_lapse_reduces_net_but_yields_zero_renewal_income` |
| 16 | Changing Latest Forecast does not change Budget | `test_16_changing_latest_forecast_does_not_change_budget` |
| 17 | A budget override affects only its intended scope | `test_17_budget_override_affects_only_its_scope` |
| 18 | Every summary reconciles to its drill-down | `test_18_summary_reconciles_to_drilldown` |
| 19 | Acceptance uses the exact figures shown in preview | `test_19_accept_uses_the_exact_previewed_figures` |
| 20 | Exports reconcile to the filtered dashboard | `test_20_export_reconciles_to_the_filtered_dashboard` |

Plus permissions, GST statement presence on every financial endpoint, and the
base-state guard that runs last.

---

## Exports

`GET /api/export/{dataset}?fmt=csv|xlsx` applies the same filters as the screen.
Every export carries a preamble with the GST statement, the reporting cut-off
date, the report timestamp, the timezone, the active filters and a note that
unavailable measures appear as N/A rather than blank or zero. A measure-kind row
sits above the column headers labelling each column Original Forecast, Latest
Forecast, Actual, Budget, Outlook or Forecast Movement, so an exported column
cannot be mistaken for a different kind of number.

---

## Next

Remaining work is the end-to-end browser suite (Playwright against the built
SPA), and matching accuracy measurement, which still needs a forecast period
overlapping transacted actuals — the next Renewals Pending snapshot supplies
that naturally.
