"""File type detection, column profiling and mapping resolution.

The user uploads a file without telling the system what it is. Detection scores
the header against each known report signature rather than trusting the
filename, which in this business is unreliable — the supplied
"Sales_Transaction_List_25-26.csv" actually spans May 2025 to July 2026, three
financial years.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

# --- report signatures -------------------------------------------------------
# `required` must all be present for the file to be importable.
# `strong` are fields distinctive enough to identify the report type.


@dataclass(frozen=True)
class ReportSignature:
    file_type: str
    label: str
    required: tuple[str, ...]
    strong: tuple[str, ...]
    optional: tuple[str, ...] = ()


SALES = ReportSignature(
    file_type="sales",
    label="Sales Transaction Report",
    required=("TransactionDate", "Category", "Commission", "Fees", "Group1Abbrev",
              # Income now derives from this column, so a file without it
              # cannot be reported on and must not be accepted.
              "PrimaryAssocAmount"),
    strong=("InvNumber", "SubComm", "PrimaryAssocCode", "TransactionDate", "Category"),
    optional=("Group1ID", "Group2ID", "Group2Description", "ClientID", "Code", "Username",
              "PolicyNumber", "Premium", "Nett", "PrimaryAssocAmount", "SecondaryAssocCode",
              "SecondaryAssocAmount", "UWCode", "PolicyClass", "Reason", "SpecialFeePrompt",
              "SpecialFees", "AdminFeePrompt", "Fee"),
)

RENEWALS = ReportSignature(
    file_type="renewals",
    label="Renewals Pending Report",
    required=("PolicyID", "ExpiryDate", "Comm", "CommTax", "Fee", "FeeTax",
              "PrimaryAssocCommSum", "PrimaryAssocCommTaxSum",
              "PolicyAccountManager"),
    strong=("PolicyID", "NextExpiryDate", "PolicyAccountManager", "RenewalMonths",
            "UnderwriterAbbrev"),
    optional=("Group1ID", "Group1Abbrev", "GroupDescription", "ClientCode", "ClientID",
              "ClassAbbrev", "ClassCode", "ClassDescription", "PolicyNumber",
              "InceptionDate", "Premium", "TotalPremium", "Admin", "AdminTax",
              "Special", "SpecialTax", "PrimaryAssocAbbrev", "SecondaryAssocAbbrev"),
)

LEGACY = ReportSignature(
    file_type="legacy_forecast",
    label="Legacy Dashboard Forecast",
    required=("forecast_month", "source_manager", "forecast_amount"),
    strong=("forecast_month", "forecast_amount", "promoted_to_original"),
)

SIGNATURES = (SALES, RENEWALS, LEGACY)

# Fields that must never be summed into income, kept here so the reason travels
# with the code rather than living only in a document.
# Columns retained for audit that must never be added into a reported figure.
#
# The reasons were written when income was Commission + Fees, and warned about a
# double count. Income is now the primary associate share, so Fees and its
# components take no part in the calculation at all -- the warning described a
# mistake that could no longer be made, which is its own kind of misleading.
NEVER_SUM = {
    "sales": {
        "SpecialFees": "component of Fees, and Fees no longer contributes to "
                       "reported income; retained for audit only",
        "Fee": "component of Fees, and Fees no longer contributes to reported "
               "income; retained for audit only",
        "Commission": "retained for audit and WinBEAT reconciliation; reported "
                      "income is the primary associate share, not commission",
        "Fees": "retained for audit and WinBEAT reconciliation; reported income "
                "is the primary associate share",
    },
    "renewals": {
        "Admin": "component of Fee, and Fee no longer contributes to expected "
                 "income; retained for audit only",
        "AdminTax": "component of FeeTax; retained for audit only",
        "Special": "component of Fee; retained for audit only",
        "SpecialTax": "component of FeeTax; retained for audit only",
        "Comm": "retained for audit; expected income is the primary associate "
                "commission plus its tax",
    },
}


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    non_null: int
    null_count: int
    sample: list[str] = field(default_factory=list)


@dataclass
class Detection:
    file_type: str | None
    label: str | None
    confidence: float
    scores: dict[str, float]
    columns: list[ColumnProfile]
    missing_required: list[str]
    unmapped_columns: list[str]
    row_count: int
    messages: list[str] = field(default_factory=list)

    @property
    def importable(self) -> bool:
        return self.file_type is not None and not self.missing_required


def read_source(path: str | Path, sample_rows: int | None = None) -> pl.DataFrame:
    """Read CSV or XLSX into a DataFrame with everything as text.

    Reading as text is deliberate. Type inference on a mixed column silently
    coerces or nulls values; parsing is done explicitly per field so a bad value
    becomes a visible rejection rather than a quiet zero.
    """
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".csv", ".txt"}:
        return pl.read_csv(p, infer_schema_length=0, n_rows=sample_rows,
                           truncate_ragged_lines=True)
    if suffix in {".xlsx", ".xlsm"}:
        df = pl.read_excel(p, infer_schema_length=0)
        return df.head(sample_rows) if sample_rows else df
    raise ValueError(f"unsupported file type: {suffix}")


def profile_columns(df: pl.DataFrame, sample_size: int = 3) -> list[ColumnProfile]:
    profiles = []
    for name in df.columns:
        col = df[name]
        non_null = int(col.drop_nulls().len())
        samples = [str(v) for v in col.drop_nulls().head(sample_size).to_list()]
        profiles.append(ColumnProfile(
            name=name, dtype=str(col.dtype), non_null=non_null,
            null_count=int(col.len()) - non_null, sample=samples))
    return profiles


def _score(headers: set[str], sig: ReportSignature) -> float:
    if not sig.strong:
        return 0.0
    strong_hits = sum(1 for c in sig.strong if c in headers)
    required_hits = sum(1 for c in sig.required if c in headers)
    return 0.6 * (strong_hits / len(sig.strong)) + 0.4 * (required_hits / len(sig.required))


def detect(path: str | Path, mapping: dict[str, str] | None = None) -> Detection:
    """Identify the report type and report what is missing.

    `mapping` renames source columns to canonical names before scoring, so a
    renamed insurer column can be resolved by an administrator without code
    changes.
    """
    df = read_source(path)
    if mapping:
        df = df.rename({k: v for k, v in mapping.items() if k in df.columns})

    headers = set(df.columns)
    scores = {sig.file_type: round(_score(headers, sig), 3) for sig in SIGNATURES}
    best_type = max(scores, key=scores.get)
    best = next(s for s in SIGNATURES if s.file_type == best_type)
    confidence = scores[best_type]

    messages: list[str] = []
    if confidence < 0.5:
        return Detection(None, None, confidence, scores, profile_columns(df), [],
                         sorted(headers), df.height,
                         ["Could not identify the report type. Supply a column mapping."])

    missing = [c for c in best.required if c not in headers]
    known = set(best.required) | set(best.strong) | set(best.optional)
    unmapped = sorted(headers - known)

    if missing:
        messages.append("Missing required columns: " + ", ".join(missing))
    if unmapped:
        messages.append(f"{len(unmapped)} column(s) present in the file are not used: "
                        + ", ".join(unmapped[:8]) + ("..." if len(unmapped) > 8 else ""))
    for col, why in NEVER_SUM.get(best_type, {}).items():
        if col in headers:
            messages.append(f"'{col}' is retained for reference only ({why}).")

    return Detection(best.file_type, best.label, confidence, scores,
                     profile_columns(df), missing, unmapped, df.height, messages)
