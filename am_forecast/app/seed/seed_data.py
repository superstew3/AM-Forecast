"""Stage 1 reference data.

Everything the calculation layer depends on is declared once, here, and loaded
into the reference tables. Nothing below is repeated in a query.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

# --- canonical reporting managers -------------------------------------------
# (canonical, status, include_in_rankings, include_in_business_totals, order, note)

REPORTING_MANAGERS = [
    ("Michael Stewart",  "active", True,  True,  10, None),
    ("Sam Stewart",      "active", True,  True,  20, None),
    ("Maddie Commins",   "active", True,  True,  30, None),
    ("Liam Thornton",    "active", True,  True,  40, None),
    ("Shannen Giles",    "active", True,  True,  50, None),
    ("Retail",           "active", True,  True,  60, "SIG Retail and Peninsula Retail combined"),
    ("AnneM Goodchild",  "active", True,  True,  70, None),
    ("Thomasina Troumb", "active", True,  True,  80, None),
    ("Rebekah Shone",    "active", True,  True,  90, "Reported separately by instruction"),
    ("Houseboats SIG",   "active", True,  True, 100, "Scheme book, reported separately"),
    ("Strata Insurance", "active", True,  True, 110, "Reported separately by instruction"),
    ("Marine Trades",    "active", True,  True, 120, "Scheme book, reported separately"),
    ("Dinghy Scheme",    "active", True,  True, 130, "Scheme book, reported separately"),
    # Actual income and forecast count towards business totals; excluded from
    # rankings to match Anastasia K's treatment.
    ("Cameron Stewart",  "active", False,  True, 140,
     "Non-Highview records only. Highview-associated Cameron Stewart rows are excluded "
     "by rule, not by manager name. Out of rankings by instruction."),
    # Actual income counts towards business totals; no pending book, so no
    # forecast and no budget. Out of rankings until an administrator maps her.
    ("Anastasia K", "legacy_unmapped", False, True, 900,
     "Legacy / unmapped. Holds actual income but no pending renewal policies, so "
     "forecast, budget and achievement are N/A. Transactions carry MMSTEWART as "
     "primary associate; that alone is not grounds to map her to Michael Stewart. "
     "Awaiting administrator decision."),
]

# --- source manager -> canonical --------------------------------------------
# The 13 confirmed mappings, plus identity rows so every source value observed
# in either file resolves through this one table rather than falling through.

MANAGER_ALIASES = [
    # confirmed combining mappings
    ("Sam Stewart",      "Sam Stewart",      None),
    ("Sam Peninsula",    "Sam Stewart",      "Peninsula office"),
    ("Michael Stewart",  "Michael Stewart",  None),
    ("MichaelPeninsula", "Michael Stewart",  "Peninsula office"),
    ("Liam Thornton",    "Liam Thornton",    None),
    ("Liam Peninsula",   "Liam Thornton",    "Peninsula office"),
    ("Shannen SIG",      "Shannen Giles",    None),
    ("ShannenPeninsula", "Shannen Giles",    "Peninsula office"),
    ("Shannen Giles",    "Shannen Giles",    None),
    ("Thomasina T",      "Thomasina Troumb", None),
    ("Thomasina Troumb", "Thomasina Troumb", None),
    ("SIG Retail",       "Retail",           None),
    ("Peninsula Retail", "Retail",           "Peninsula office"),
    # separately reported, identity mapping
    ("Maddie Commins",   "Maddie Commins",   None),
    ("AnneM Goodchild",  "AnneM Goodchild",  None),
    ("Rebekah Shone",    "Rebekah Shone",    None),
    ("Strata Insurance", "Strata Insurance", None),
    ("Houseboats SIG",   "Houseboats SIG",   None),
    ("Marine Trades",    "Marine Trades",    None),
    ("Dinghy Scheme",    "Dinghy Scheme",    None),
    ("Cameron Stewart",  "Cameron Stewart",  None),
    ("Anastasia K",      "Anastasia K",      "Legacy / unmapped"),
]

# --- Highview exclusion rules ------------------------------------------------
# Values are stored already normalised: uppercased, punctuation to space,
# repeated whitespace collapsed, trimmed. Ingest normalises the source field
# identically before comparing.

_MANAGER_MATCH = "CAM HIGHVIEW"
_ASSOC_MATCHES = ("HIGHVIEW", "CAMHIGH", "SIG HIGH")

EXCLUSION_RULES = (
    [("highview", f"Sales manager is {_MANAGER_MATCH}", "sales", "Group1Abbrev",
      "exact", _MANAGER_MATCH, "Source account manager field")]
    + [("highview", f"Sales primary associate contains {v}", "sales", "PrimaryAssocCode",
        "contains", v, None) for v in _ASSOC_MATCHES]
    + [("highview", f"Sales secondary associate contains {v}", "sales", "SecondaryAssocCode",
        "contains", v, None) for v in _ASSOC_MATCHES]
    + [("highview", f"Renewals manager is {_MANAGER_MATCH}", "renewals",
        "PolicyAccountManager", "exact", _MANAGER_MATCH, "Source account manager field"),
       ("highview", f"Renewals group is {_MANAGER_MATCH}", "renewals", "Group1Abbrev",
        "exact", _MANAGER_MATCH,
        "Currently identical to PolicyAccountManager on 100% of supplied rows. "
        "Retained as a defensive rule in case the two fields diverge.")]
    + [("highview", f"Renewals primary associate contains {v}", "renewals",
        "PrimaryAssocAbbrev", "contains", v, None) for v in _ASSOC_MATCHES]
    + [("highview", f"Renewals secondary associate contains {v}", "renewals",
        "SecondaryAssocAbbrev", "contains", v, None) for v in _ASSOC_MATCHES]
)

# --- transaction category map ------------------------------------------------

CATEGORY_MAP = [
    ("RWL", "Renewal", "Renewal"),
    ("TRW", "Transfer Renewal", "Transfer renewal"),
    ("N/B", "New Business", "New business"),
    ("END", "Endorsement", "Endorsement, positive or negative"),
    ("LAP", "Lapse / End-Term Lost Renewal",
     "Lapse, end-term cancellation or lost renewal. The Reason field is never used "
     "to subdivide or reinterpret these."),
    ("MCN", "Mid-Term Cancellation", "Mid-term cancellation"),
    ("NCN", "New Business Cancellation", "Cancelled new business"),
    ("ADJ", "Adjustment", "Adjustment or correction, positive or negative"),
    ("ECN", "Endorsement Cancellation", "Endorsement cancellation"),
    ("CCN", "Policy Reinstatement", "Policy reinstatement, may be positive or negative"),
    # Appears in later exports as a cancellation raised to correct an error.
    # Always negative, so it belongs with the other cancellations rather than
    # with lapses, which represent lost business.
    ("CLN", "Mid-Term Cancellation", "Cancellation, typically an error correction"),
]

# --- forecast baselines ------------------------------------------------------
# July 2026 is sourced from the legacy dashboard at manager-month grain.
# August 2026 onward comes from the Renewals Pending snapshot at policy grain.

_LEGACY = "Legacy Dashboard Forecast"
_SNAPSHOT = "Renewals Pending Snapshot"

_JULY_MANAGER_EXCEPTIONS = [
    "Cameron Stewart",   # legacy line cannot be shown to be Highview-free
    "Dinghy Scheme",     # no legacy forecast row
    "Anastasia K",       # no legacy forecast row and no pending book
]

_Q1_NOTE = (
    "FY2026-27 Q1 has a mixed original forecast baseline. July is carried from the "
    "legacy dashboard at manager-month grain; August and September come from the "
    "Renewals Pending snapshot at policy grain. Policy-level renewal achievement is "
    "reliable from August 2026 onward."
)


def _months(start: dt.date, end: dt.date):
    m = start
    while m <= end:
        yield m
        m = dt.date(m.year + (m.month == 12), (m.month % 12) + 1, 1)


def forecast_baselines() -> list[dict]:
    rows = []
    for m in _months(dt.date(2026, 7, 1), dt.date(2027, 6, 1)):
        july = m == dt.date(2026, 7, 1)
        rows.append({
            "forecast_month": m,
            "baseline_status": "complete",
            "baseline_source": _LEGACY if july else _SNAPSHOT,
            "suppress_achievement": False,
            "manager_exceptions": _JULY_MANAGER_EXCEPTIONS if july else [],
            "note": _Q1_NOTE if m <= dt.date(2026, 9, 1) else None,
        })
    return rows


# FY2025-26 legacy forecast covers November 2025 to June 2026 only. July to
# October 2025 have no baseline at all and must report N/A, not zero.
def fy2025_26_baselines() -> list[dict]:
    rows = []
    for m in _months(dt.date(2025, 7, 1), dt.date(2026, 6, 1)):
        has = m >= dt.date(2025, 11, 1)
        rows.append({
            "forecast_month": m,
            "baseline_status": "complete" if has else "unavailable",
            "baseline_source": _LEGACY if has else None,
            "suppress_achievement": not has,
            "manager_exceptions": ["Cameron Stewart", "Dinghy Scheme", "Anastasia K"] if has else [],
            "note": None if has else
                    "No original forecast supplied for this month. The legacy dashboard "
                    "forecast series begins November 2025.",
        })
    return rows


# --- period coverage ---------------------------------------------------------

PERIOD_COVERAGE = [
    (2024, "actuals", "partial", 2, dt.date(2025, 5, 1), dt.date(2025, 6, 1),
     "FY2024-25 partial period, May to June 2025 only. Not a full financial year "
     "and must not be compared as one."),
    (2025, "actuals", "complete", 12, dt.date(2025, 7, 1), dt.date(2026, 6, 1),
     "FY2025-26 complete."),
    (2026, "actuals", "partial", 1, dt.date(2026, 7, 1), dt.date(2026, 7, 1),
     "FY2026-27 in progress. July 2026 complete as at the reporting cut-off."),
    (2026, "forecast", "complete", 12, dt.date(2026, 7, 1), dt.date(2027, 6, 1),
     "FY2026-27 original forecast. July from legacy dashboard, August onward from "
     "the Renewals Pending snapshot."),
    (2025, "forecast", "partial", 8, dt.date(2025, 11, 1), dt.date(2026, 6, 1),
     "FY2025-26 legacy forecast covers November 2025 to June 2026 only."),
]

# --- settings ----------------------------------------------------------------

REPORTING_SETTINGS = {
    # Initial value only: the seed no longer overwrites this on re-run. An
    # administrator moves it forward as each month closes, on the Settings page.
    #
    # It must sit at the end of the last month that is genuinely complete. Set
    # too far forward, a month still being transacted is treated as closed: its
    # renewals are never promoted to the forecast, so there is no baseline and
    # no budget, and the cause is not obvious from any screen.
    "cut_off_date": dt.date(2026, 7, 31),
    "match_date_tolerance_days": 45,
    "default_growth_pct": Decimal("0.0750"),
    "gst_note": "All income figures are GST inclusive.",
}

DEFAULT_GROWTH_RATE = {
    "scope": "global",
    "growth_pct": Decimal("0.0750"),
    "note": "Initial default new business growth target, applied to the Original "
            "Renewal Forecast. Adjustable globally, by manager, by manager and "
            "quarter, or by direct dollar override.",
}
