"""Value normalisation and coercion.

One implementation, used by every importer and by the seed loader, so a rule
value and the source field it is compared against are normalised identically.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from decimal import Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0.00")

_DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%m/%d/%Y",
)

_CURRENCY_STRIP = re.compile(r"[$,\s]")
_PARENS = re.compile(r"^\((.*)\)$")


def norm(value) -> str:
    """Uppercase, punctuation to space, whitespace collapsed, trimmed.

    Used for manager and associate matching on both sides of the comparison.
    """
    if value is None:
        return ""
    s = str(value).upper()
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm_policy(value) -> str | None:
    """Conservative policy-number normalisation.

    Trim, uppercase, collapse internal whitespace. Separators are deliberately
    retained: values like 132SV05584VSD and MOVPOL11116034 carry meaningful
    embedded structure, and stripping it risks joining unrelated policies.
    """
    if value is None:
        return None
    s = re.sub(r"\s+", " ", str(value).strip().upper())
    return s or None


def dec(value) -> Decimal:
    """Currency to Decimal. Handles $, thousands separators and (123.45)."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value.quantize(CENT)
    if isinstance(value, (int, float)):
        return Decimal(str(value)).quantize(CENT)
    s = str(value).strip()
    if not s:
        return ZERO
    negative = False
    m = _PARENS.match(s)
    if m:
        negative, s = True, m.group(1)
    s = _CURRENCY_STRIP.sub("", s)
    if not s or s in {"-", "."}:
        return ZERO
    try:
        d = Decimal(s).quantize(CENT)
    except InvalidOperation as exc:
        raise ValueError(f"not a currency value: {value!r}") from exc
    return -d if negative else d


def intn(value) -> int | None:
    """Nullable integer. Empty source cells become NULL, never zero."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def parse_datetime(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day)
    s = str(value).strip()
    if not s:
        raise ValueError("empty date")
    for fmt in _DATE_FORMATS:
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    # Trailing fractional seconds or timezone suffix.
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise ValueError(f"unrecognised date format: {value!r}") from exc


def parse_date(value) -> dt.date:
    return parse_datetime(value).date()


def month_start(d: dt.date) -> dt.date:
    return d.replace(day=1)


def australian_fy(d: dt.date) -> int:
    """Starting calendar year of the Australian financial year."""
    return d.year if d.month >= 7 else d.year - 1


def australian_quarter(d: dt.date) -> int:
    """Q1 Jul-Sep, Q2 Oct-Dec, Q3 Jan-Mar, Q4 Apr-Jun."""
    return ((d.month - 7) % 12) // 3 + 1


FINGERPRINT_FIELDS = (
    "InvNumber", "TransactionDate", "Code", "PolicyNumber",
    "Category", "Commission", "Fees", "Group1Abbrev",
)


def transaction_fingerprint(row: dict) -> str:
    """Stable identity for a sales transaction line.

    Validated on the supplied file: 14,886 rows produce 14,886 distinct values,
    zero collisions, including across the extended field set. Invoice number
    alone is never sufficient — up to six legitimate lines share one.

    Numeric fields are normalised through Decimal so that 55.6, "55.60" and
    " 55.60 " fingerprint identically. Without that, a source export that
    changes its number formatting would duplicate every row on re-upload.
    """
    parts = []
    for field in FINGERPRINT_FIELDS:
        value = row.get(field)
        if field in {"Commission", "Fees"}:
            parts.append(str(dec(value)))
        elif field == "TransactionDate":
            try:
                parts.append(parse_datetime(value).isoformat())
            except ValueError:
                parts.append("")
        else:
            parts.append("" if value is None else str(value).strip().lower())
    return hashlib.sha256("|".join(parts).encode()).hexdigest()
