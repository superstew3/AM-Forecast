"""Import service.

Two phases. `prepare` parses, validates and stages every row without touching a
fact table. `accept` promotes the staged rows. `reject` discards them. Nothing
in between mutates reported figures, so the pre-import summary a user approves
is computed from exactly the rows that will land.

`rollback` reverses an accepted batch. For cumulative sales reports this is only
deterministic because every sighting is recorded — see models/staging.py.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from psycopg2.extras import Json, execute_batch

from .detect import RENEWALS, SALES, detect, read_source
from .engine import (
    ExclusionEngine, classify, load_alias_map, load_category_map, resolve_manager,
)
from .normalise import (
    ZERO, australian_fy, australian_quarter, dec, intn, month_start, norm,
    norm_policy, parse_date, parse_datetime, transaction_fingerprint,
)

# Non-key fields watched for restatement. A change here means the source
# restated a transaction that already exists, which is a decision for a human,
# not a silent overwrite.
RESTATEMENT_WATCH = ("Premium", "Nett", "SubComm", "PolicyClass", "UWCode",
                     "PrimaryAssocCode", "SecondaryAssocCode", "Reason")


class ImportError_(Exception):
    """Raised when a batch cannot proceed."""


@dataclass
class PreviewSummary:
    batch_id: int
    file_name: str
    file_type: str
    label: str
    detection_confidence: float
    source_rows: int = 0
    valid_rows: int = 0
    duplicate_rows: int = 0
    excluded_rows: int = 0
    rejected_rows: int = 0
    restated_rows: int = 0
    positive_income: Decimal = ZERO
    return_income: Decimal = ZERO
    net_income: Decimal = ZERO
    raw_expected_income: Decimal = ZERO
    forecast_contribution: Decimal = ZERO
    coverage_start: dt.date | None = None
    coverage_end: dt.date | None = None
    exception_count: int = 0
    exceptions_by_type: dict[str, int] = field(default_factory=dict)
    messages: list[str] = field(default_factory=list)
    requires_confirmation: bool = False
    coverage: object | None = None

    def render(self) -> str:
        lines = [
            f"Batch {self.batch_id}: {self.file_name}",
            f"  Detected: {self.label} (confidence {self.detection_confidence:.0%})",
            f"  Source rows            {self.source_rows:>10,}",
            f"  Valid                  {self.valid_rows:>10,}",
            f"  Highview excluded      {self.excluded_rows:>10,}",
            f"  Duplicates             {self.duplicate_rows:>10,}",
            f"  Restated               {self.restated_rows:>10,}",
            f"  Rejected               {self.rejected_rows:>10,}",
        ]
        if self.file_type == "sales":
            lines += [
                f"  Positive income        {self.positive_income:>14,.2f}",
                f"  Return income          {self.return_income:>14,.2f}",
                f"  Net income             {self.net_income:>14,.2f}",
            ]
        else:
            lines += [
                f"  Raw expected income    {self.raw_expected_income:>14,.2f}",
                f"  Forecast contribution  {self.forecast_contribution:>14,.2f}",
            ]
        if self.coverage_start:
            lines.append(f"  Coverage               {self.coverage_start} to {self.coverage_end}")
        if self.coverage is not None:
            lines.append(self.coverage.render())
        lines.append(f"  Exceptions             {self.exception_count:>10,}")
        for k, v in sorted(self.exceptions_by_type.items()):
            lines.append(f"    {k:<28} {v:>6,}")
        for m in self.messages:
            lines.append(f"  ! {m}")
        if self.requires_confirmation:
            lines.append("  ** CONFIRMATION REQUIRED before this batch can be accepted. **")
        lines.append("  All income figures are GST inclusive.")
        return "\n".join(lines)


# --- helpers -----------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _add_exception(bucket: list, batch_id, etype, severity, row_no, field_, value, msg,
                   payload=None):
    bucket.append((batch_id, etype, severity, row_no, field_,
                   None if value is None else str(value)[:500], msg,
                   Json(payload) if payload is not None else None))


def _flush_exceptions(cur, bucket):
    if not bucket:
        return
    execute_batch(cur, """
        INSERT INTO ingest_exception
          (batch_id, exception_type, severity, source_row_number, field_name,
           field_value, message, payload)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    """, bucket, page_size=500)


# --- prepare -----------------------------------------------------------------

def prepare(conn, path: str | Path, uploaded_by: str,
            mapping: dict[str, str] | None = None,
            as_of: dt.date | None = None) -> PreviewSummary:
    """Parse, validate and stage a file. Touches no fact table."""
    p = Path(path)
    det = detect(p, mapping)
    if det.file_type is None:
        raise ImportError_("; ".join(det.messages) or "Unrecognised report type")
    if det.missing_required:
        raise ImportError_("Missing required columns: " + ", ".join(det.missing_required))

    digest = _sha256(p)
    with conn.cursor() as cur:
        cur.execute("""SELECT id, status FROM upload_batch
                       WHERE file_sha256 = %s AND status = 'accepted'""", (digest,))
        prior = cur.fetchone()
        prior_note = (f"Byte-identical to accepted batch {prior[0]}. "
                      "Re-importing changes no total; every row will register as a "
                      "duplicate sighting.") if prior else None

        cur.execute("""
            INSERT INTO upload_batch
              (file_name, file_type, file_sha256, file_size_bytes, uploaded_by,
               status, source_row_count, column_mapping)
            VALUES (%s,%s,%s,%s,%s,'pending',%s,%s) RETURNING id
        """, (p.name, det.file_type, digest, p.stat().st_size, uploaded_by,
              det.row_count, Json(mapping or {})))
        batch_id = cur.fetchone()[0]

        summary = PreviewSummary(batch_id=batch_id, file_name=p.name,
                                 file_type=det.file_type, label=det.label,
                                 detection_confidence=det.confidence,
                                 source_rows=det.row_count,
                                 messages=list(det.messages))
        if prior_note:
            summary.messages.append(prior_note)

        df = read_source(p)
        if mapping:
            df = df.rename({k: v for k, v in mapping.items() if k in df.columns})
        rows = df.to_dicts()

        if det.file_type == "sales":
            _stage_sales(cur, batch_id, rows, summary)
        elif det.file_type == "renewals":
            _stage_renewals(cur, batch_id, rows, summary,
                            as_of or _default_as_of(cur))
            _attach_coverage(cur, batch_id, summary)
        else:
            _stage_legacy(cur, batch_id, rows, summary)

        cur.execute("""
            UPDATE upload_batch SET
              accepted_row_count=%s, duplicate_row_count=%s, excluded_row_count=%s,
              rejected_row_count=%s, positive_income=%s, return_income=%s,
              net_income=%s, expected_forecast_income=%s, exception_count=%s,
              coverage_start=%s, coverage_end=%s, validation_messages=%s
            WHERE id=%s
        """, (summary.valid_rows, summary.duplicate_rows, summary.excluded_rows,
              summary.rejected_rows, summary.positive_income, summary.return_income,
              summary.net_income, summary.forecast_contribution, summary.exception_count,
              summary.coverage_start, summary.coverage_end,
              Json(summary.messages), batch_id))
    conn.commit()
    return summary


def _attach_coverage(cur, batch_id: int, s: PreviewSummary) -> None:
    """Add month coverage and any mass-removal warnings to the preview."""
    from ..forecast.coverage import analyse_staged_coverage
    report = analyse_staged_coverage(cur, batch_id)
    s.coverage = report
    s.requires_confirmation = report.requires_confirmation
    s.messages.extend(report.warnings)
    cur.execute("""UPDATE upload_batch SET requires_confirmation=%s, coverage_warnings=%s
                   WHERE id=%s""",
                (report.requires_confirmation, Json(report.warnings), batch_id))


def _default_as_of(cur) -> dt.date:
    cur.execute("SELECT cut_off_date FROM reporting_settings WHERE id = 1")
    row = cur.fetchone()
    return row[0] if row else dt.date.today()


# --- sales staging -----------------------------------------------------------

def _stage_sales(cur, batch_id: int, rows: list[dict], s: PreviewSummary) -> None:
    engine = ExclusionEngine.load(cur, "sales")
    cat_map = load_category_map(cur)
    alias_map = load_alias_map(cur)

    cur.execute("SELECT fingerprint, id, source_row FROM sales_transaction")
    existing = {fp: (tid, src) for fp, tid, src in cur.fetchall()}

    staged, exceptions, seen_in_file = [], [], {}
    unknown_managers, unknown_categories = set(), set()

    for i, r in enumerate(rows, start=1):
        try:
            td = parse_datetime(r["TransactionDate"])
            commission, fees = dec(r.get("Commission")), dec(r.get("Fees"))
            # SIG income is the primary associate share, not the gross figure.
            # Already GST inclusive: this report carries no tax column.
            primary_assoc = dec(r.get("PrimaryAssocAmount"))
        except (ValueError, KeyError) as exc:
            s.rejected_rows += 1
            _add_exception(exceptions, batch_id, "invalid_value", "error", i, None, None,
                           f"Row rejected: {exc}")
            staged.append((batch_id, i, "rejected", None, None, None, None, None, None,
                           None, None, None, None, None, False, None, None, None,
                           ["parse_error"], str(exc), None, Json({}), Json(r)))
            continue

        # Reported income is the associate amount. Commission and fees are
        # retained on the row for audit and reconciliation, but they are the
        # gross brokerage figure and overstate what the business receives.
        amount = primary_assoc
        fp = transaction_fingerprint(r)
        hit = engine.check(r)
        business, derived, fin_dir, unmapped = classify(r.get("Category"), amount, cat_map)
        canonical = resolve_manager(r.get("Group1Abbrev"), alias_map)

        flags: list[str] = []
        if unmapped:
            flags.append("unmapped_category")
            unknown_categories.add(str(r.get("Category")))
        if canonical is None and not hit:
            flags.append("missing_manager_mapping")
            unknown_managers.add(str(r.get("Group1Abbrev")))

        status, existing_id, changed = "valid", None, None
        if fp in seen_in_file:
            status = "duplicate"
            flags.append("duplicate_within_file")
        elif fp in existing:
            existing_id, prior_src = existing[fp]
            changed = {k: [prior_src.get(k), r.get(k)] for k in RESTATEMENT_WATCH
                       if str(prior_src.get(k)) != str(r.get(k))}
            if changed:
                status = "restated"
                flags.append("restated")
                s.restated_rows += 1
                _add_exception(exceptions, batch_id, "restated_transaction", "warning", i,
                               None, fp[:16],
                               "Fingerprint already present with different supporting "
                               "values. Held for review; not applied automatically.",
                               changed)
            else:
                status = "duplicate"
        seen_in_file[fp] = i

        if status == "valid" and hit:
            status = "excluded"

        if status in ("valid", "excluded"):
            if hit:
                s.excluded_rows += 1
            else:
                s.positive_income += max(amount, ZERO)
                s.return_income += min(amount, ZERO)
            s.valid_rows += 1
            m = month_start(td.date())
            s.coverage_start = m if s.coverage_start is None else min(s.coverage_start, m)
            s.coverage_end = (td.date() if s.coverage_end is None
                              else max(s.coverage_end, td.date()))
        elif status in ("duplicate", "restated"):
            s.duplicate_rows += 1

        prepared = {
            "fingerprint": fp,
            "transaction_date": td.isoformat(),
            "period_month": month_start(td.date()).isoformat(),
            "financial_year": australian_fy(td.date()),
            "financial_quarter": australian_quarter(td.date()),
            "source_manager": r.get("Group1Abbrev"),
            "group1_id": intn(r.get("Group1ID")),
            "group2_description": r.get("Group2Description"),
            "client_id": intn(r.get("ClientID")),
            "client_code": r.get("Code"), "client_code_norm": norm(r.get("Code")),
            "policy_number": r.get("PolicyNumber"),
            "policy_number_norm": norm_policy(r.get("PolicyNumber")),
            "invoice_number": intn(r.get("InvNumber")),
            "username": r.get("Username"),
            "category": r.get("Category"),
            "business_classification": business,
            "derived_classification": derived,
            "policy_class": r.get("PolicyClass"), "uw_code": r.get("UWCode"),
            "reason": r.get("Reason"),
            "premium": str(dec(r.get("Premium"))), "nett": str(dec(r.get("Nett"))),
            "commission": str(commission), "fees": str(fees),
            "sub_comm": str(dec(r.get("SubComm"))),
            "financial_direction": fin_dir,
            "primary_assoc_code": r.get("PrimaryAssocCode"),
            "primary_assoc_amount": str(dec(r.get("PrimaryAssocAmount"))),
            "secondary_assoc_code": r.get("SecondaryAssocCode"),
            "secondary_assoc_amount": str(dec(r.get("SecondaryAssocAmount"))),
            "is_excluded": hit is not None,
            "exclusion_rule_id": hit.rule_id if hit else None,
            "exclusion_field": hit.field if hit else None,
            "exclusion_value": hit.value if hit else None,
        }
        staged.append((
            batch_id, i, status, fp, existing_id, None,
            month_start(td.date()), r.get("Group1Abbrev"), r.get("Category"),
            max(amount, ZERO) if not hit else ZERO,
            min(amount, ZERO) if not hit else ZERO,
            amount if not hit else ZERO,
            None, None,
            hit is not None, hit.rule_id if hit else None,
            hit.field if hit else None, hit.value if hit else None,
            flags, None, Json(changed) if changed else None, Json(prepared), Json(r),
        ))

    s.net_income = s.positive_income + s.return_income
    for m in unknown_managers:
        _add_exception(exceptions, batch_id, "missing_manager_mapping", "error", None,
                       "Group1Abbrev", m,
                       f"Source manager '{m}' has no alias mapping. Add one before "
                       "accepting, or its income will not roll up to any manager.")
    for c in unknown_categories:
        _add_exception(exceptions, batch_id, "unmapped_category", "error", None,
                       "Category", c,
                       f"Category '{c}' is not in the category map.")
    _write_staging(cur, staged)
    _flush_exceptions(cur, exceptions)
    s.exception_count = len(exceptions)
    s.exceptions_by_type = _count_types(exceptions)


# --- renewals staging --------------------------------------------------------

def _stage_renewals(cur, batch_id: int, rows: list[dict], s: PreviewSummary,
                    as_of: dt.date) -> None:
    engine = ExclusionEngine.load(cur, "renewals")
    alias_map = load_alias_map(cur)

    staged, exceptions, seen_ids = [], [], {}
    unknown_managers = set()
    cut_month = month_start(as_of)

    for i, r in enumerate(rows, start=1):
        try:
            expiry = parse_date(r["ExpiryDate"])
            comm, comm_tax = dec(r.get("Comm")), dec(r.get("CommTax"))
            fee, fee_tax = dec(r.get("Fee")), dec(r.get("FeeTax"))
            # The associate columns are the SIG share. CommSum is GST
            # exclusive and CommTaxSum is its GST, so both are needed to match
            # the GST-inclusive sales figures.
            pa_comm_sum = dec(r.get("PrimaryAssocCommSum"))
            pa_comm_tax_sum = dec(r.get("PrimaryAssocCommTaxSum"))
            policy_id = intn(r.get("PolicyID"))
            if policy_id is None:
                raise ValueError("PolicyID is missing")
        except (ValueError, KeyError) as exc:
            s.rejected_rows += 1
            _add_exception(exceptions, batch_id, "invalid_value", "error", i, None, None,
                           f"Row rejected: {exc}")
            staged.append((batch_id, i, "rejected", None, None, None, None, None, None,
                           None, None, None, None, None, False, None, None, None,
                           ["parse_error"], str(exc), None, Json({}), Json(r)))
            continue

        # Gross would be comm + comm_tax + fee + fee_tax. Expected income is
        # the associate share instead, GST inclusive.
        raw = pa_comm_sum + pa_comm_tax_sum
        contribution = max(raw, ZERO)
        hit = engine.check(r)
        canonical = resolve_manager(r.get("PolicyAccountManager"), alias_map)

        flags: list[str] = []
        status = "excluded" if hit else "valid"

        # PolicyID must be unique within a snapshot. Repeats indicate a
        # malformed export and are rejected rather than silently collapsed.
        if policy_id in seen_ids:
            status = "rejected"
            s.rejected_rows += 1
            flags.append("duplicate_policy_id")
            _add_exception(exceptions, batch_id, "duplicate_policy_id", "error", i,
                           "PolicyID", policy_id,
                           f"PolicyID {policy_id} already appeared at row "
                           f"{seen_ids[policy_id]}. A snapshot must hold each PolicyID once.")
        else:
            seen_ids[policy_id] = i

        if status != "rejected":
            if hit:
                s.excluded_rows += 1
            else:
                if raw < 0:
                    flags.append("negative_expected")
                    _add_exception(exceptions, batch_id, "negative_expected", "warning", i,
                                   "raw_expected_income", raw,
                                   "Negative expected income. Contributes zero to the "
                                   "forecast and is retained for manual review.")
                elif raw == 0:
                    flags.append("zero_expected")
                    _add_exception(exceptions, batch_id, "zero_expected", "info", i,
                                   "raw_expected_income", raw,
                                   "Zero expected income. Contributes zero to the "
                                   "forecast and stays separately identifiable.")
                if expiry < as_of:
                    flags.append("overdue_pending")
                    _add_exception(exceptions, batch_id, "overdue_pending", "warning", i,
                                   "ExpiryDate", expiry,
                                   "Expiry precedes the snapshot date. Retained in its "
                                   "original renewal month, not moved forward.")
                if month_start(expiry) <= cut_month:
                    flags.append("residual_pending")
                if canonical is None:
                    flags.append("missing_manager_mapping")
                    unknown_managers.add(str(r.get("PolicyAccountManager")))
                s.raw_expected_income += raw
                s.forecast_contribution += contribution
                s.coverage_start = (expiry if s.coverage_start is None
                                    else min(s.coverage_start, expiry))
                s.coverage_end = (expiry if s.coverage_end is None
                                  else max(s.coverage_end, expiry))
            s.valid_rows += 1

        month = month_start(expiry)
        prepared = {
            "policy_id": policy_id,
            "client_id": intn(r.get("ClientID")), "client_code": r.get("ClientCode"),
            "client_code_norm": norm(r.get("ClientCode")),
            "policy_number": r.get("PolicyNumber"),
            "policy_number_norm": norm_policy(r.get("PolicyNumber")),
            "class_abbrev": r.get("ClassAbbrev"), "class_code": r.get("ClassCode"),
            "class_description": r.get("ClassDescription"),
            "underwriter_abbrev": r.get("UnderwriterAbbrev"),
            "inception_date": (parse_date(r["InceptionDate"]).isoformat()
                               if r.get("InceptionDate") else None),
            "expiry_date": expiry.isoformat(),
            "next_expiry_date": (parse_date(r["NextExpiryDate"]).isoformat()
                                 if r.get("NextExpiryDate") else None),
            "renewal_months": intn(r.get("RenewalMonths")),
            "forecast_month": month.isoformat(),
            "financial_year": australian_fy(month),
            "financial_quarter": australian_quarter(month),
            "source_manager": r.get("PolicyAccountManager"),
            "comm": str(comm), "comm_tax": str(comm_tax),
            "fee": str(fee), "fee_tax": str(fee_tax),
            "premium": str(dec(r.get("Premium"))),
            "total_premium": str(dec(r.get("TotalPremium"))),
            "primary_assoc_comm_sum": str(pa_comm_sum),
            "primary_assoc_comm_tax_sum": str(pa_comm_tax_sum),
            "primary_assoc_abbrev": r.get("PrimaryAssocAbbrev"),
            "exception_flags": [f for f in flags if f in
                                ("negative_expected", "zero_expected",
                                 "overdue_pending", "residual_pending")],
            "is_excluded": hit is not None,
            "exclusion_rule_id": hit.rule_id if hit else None,
            "exclusion_field": hit.field if hit else None,
            "exclusion_value": hit.value if hit else None,
        }
        staged.append((
            batch_id, i, status, None, None, policy_id, month,
            r.get("PolicyAccountManager"), None,
            None, None, None, raw if not hit else ZERO,
            contribution if not hit else ZERO,
            hit is not None, hit.rule_id if hit else None,
            hit.field if hit else None, hit.value if hit else None,
            flags, None, None, Json(prepared), Json(r),
        ))

    for m in unknown_managers:
        _add_exception(exceptions, batch_id, "missing_manager_mapping", "error", None,
                       "PolicyAccountManager", m,
                       f"Policy account manager '{m}' has no alias mapping.")
    _write_staging(cur, staged)
    _flush_exceptions(cur, exceptions)
    s.exception_count = len(exceptions)
    s.exceptions_by_type = _count_types(exceptions)


def _stage_legacy(cur, batch_id: int, rows: list[dict], s: PreviewSummary) -> None:
    staged, exceptions = [], []
    for i, r in enumerate(rows, start=1):
        try:
            month = month_start(parse_date(r["forecast_month"]))
            amount = dec(r["forecast_amount"])
        except (ValueError, KeyError) as exc:
            s.rejected_rows += 1
            _add_exception(exceptions, batch_id, "invalid_value", "error", i, None, None,
                           f"Row rejected: {exc}")
            continue
        promote = str(r.get("promoted_to_original", "")).lower() in ("true", "1", "t")
        clean = str(r.get("is_verified_exclusion_clean", "true")).lower() in ("true", "1", "t")
        s.valid_rows += 1
        s.forecast_contribution += max(amount, ZERO) if promote else ZERO
        s.coverage_start = month if s.coverage_start is None else min(s.coverage_start, month)
        s.coverage_end = month if s.coverage_end is None else max(s.coverage_end, month)
        prepared = {
            "forecast_month": month.isoformat(),
            "financial_year": australian_fy(month),
            "financial_quarter": australian_quarter(month),
            "source_manager": r.get("source_manager"),
            "forecast_amount": str(amount),
            "promoted_to_original": promote and clean,
            "is_verified_exclusion_clean": clean,
            "note": r.get("note") or None,
        }
        staged.append((batch_id, i, "valid", None, None, None, month,
                       r.get("source_manager"), None, None, None, None, amount,
                       max(amount, ZERO), False, None, None, None,
                       [] if clean else ["unverified_exclusion"], None, None,
                       Json(prepared), Json(r)))
    _write_staging(cur, staged)
    _flush_exceptions(cur, exceptions)
    s.exception_count = len(exceptions)
    s.exceptions_by_type = _count_types(exceptions)


def _count_types(exceptions) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in exceptions:
        out[e[1]] = out.get(e[1], 0) + 1
    return out


def _write_staging(cur, staged) -> None:
    execute_batch(cur, """
        INSERT INTO import_staging
          (batch_id, source_row_number, status, fingerprint, existing_transaction_id,
           policy_id, period_month, source_manager, category,
           positive_income, return_income, net_income, expected_income,
           forecast_contribution, is_excluded, exclusion_rule_id, exclusion_field,
           exclusion_value, exception_flags, reject_reason, changed_fields,
           prepared, source_row)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, staged, page_size=1000)
