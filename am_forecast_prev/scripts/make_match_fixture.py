#!/usr/bin/env python3
"""Build a synthetic Sales Transaction file that exercises the matching engine.

The supplied data cannot test matching: only July 2026 overlaps the forecast, and
it holds two residual policies. This generates transactions that deliberately hit
every path, derived from real forecast policies so the identifiers are realistic.

The output is imported as a normal batch and rolled back afterwards, so it never
contaminates the production position.

    python scripts/make_match_fixture.py <dsn> <out.csv> [--month 2026-08-01]
"""
from __future__ import annotations

import csv
import datetime as dt
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

COLUMNS = ["Group1ID", "Group1Abbrev", "Group2ID", "Group2Description", "ClientID",
           "Code", "TransactionDate", "Username", "InvNumber", "PolicyNumber",
           "Category", "Premium", "Nett", "Commission", "Fees", "SubComm",
           "PrimaryAssocCode", "PrimaryAssocAmount", "SecondaryAssocCode",
           "SecondaryAssocAmount", "UWCode", "PolicyClass", "Reason",
           "SpecialFeePrompt", "SpecialFees", "AdminFeePrompt", "Fee"]

# Renewals class -> a sales class that maps to the same canonical class.
CLASS_OUT = {
    "PLEASURE CRAFT": "PLEASURECR", "HOME INSURANCE": "HOME",
    "BUSINESS PACK": "BUSINESS", "PRIVATE MOTOR": "PRIV MOTOR",
    "COMMERCIAL MOTOR": "COMM MOTOR", "LANDLORDS": "LANDLORDS",
    "LIABILITY": "LIABILITY", "HOUSEBOAT INS": "HBOAT",
    "FARM PACKAGE": "FARM", "CARAVAN": "CARAVAN", "STRATA TITLE": "STRATA",
    "CONTRACT WORKS": "CONTRACTWK", "MARINE CARGO": "MARINECARG",
}

BASE_INVOICE = 8_800_000


def row(**kw) -> dict:
    r = {c: "" for c in COLUMNS}
    r.update({"Group1ID": "1", "Group2ID": "1", "Username": "fixture",
              "Premium": "0", "Nett": "0", "SubComm": "0",
              "PrimaryAssocAmount": "0", "SecondaryAssocAmount": "0",
              "SpecialFees": "0", "Fee": "0", "UWCode": "TEST"})
    r.update(kw)
    return r


def build(dsn: str, month: dt.date) -> list[dict]:
    conn = psycopg2.connect(dsn)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT p.policy_id, p.client_code, p.policy_number, p.class_abbrev,
                   p.expiry_date, p.source_manager, p.forecast_contribution
            FROM forecast_policy p
            WHERE NOT p.is_excluded AND p.forecast_month = %s
              AND p.forecast_contribution > 100
              AND p.class_abbrev = ANY(%s)
            ORDER BY p.policy_id
            LIMIT 60
        """, (month, list(CLASS_OUT)))
        policies = cur.fetchall()

        # Real cases where distinct PolicyIDs share client and policy number.
        # These are the duplicate-allocation hazard.
        cur.execute("""
            SELECT client_code, policy_number, array_agg(policy_id), MIN(class_abbrev),
                   MIN(expiry_date), MIN(source_manager)
            FROM forecast_policy WHERE NOT is_excluded AND forecast_month = %s
            GROUP BY 1, 2 HAVING count(DISTINCT policy_id) > 1 LIMIT 3
        """, (month,))
        contended = cur.fetchall()
    conn.close()

    if len(policies) < 40:
        raise SystemExit(f"only {len(policies)} candidate policies for {month}; "
                         "pick a different month")

    rows, inv = [], BASE_INVOICE

    def emit(policy, category, income, day_offset, invoice=None, policy_class=None):
        nonlocal inv
        _pid, client, polnum, cls, expiry, manager, _c = policy
        invoice = invoice or inv
        inv += 1
        commission = (Decimal(str(income)) * Decimal("0.8")).quantize(Decimal("0.01"))
        fees = Decimal(str(income)) - commission
        rows.append(row(
            Group1Abbrev=manager, Group2Description=category, Code=client,
            TransactionDate=(expiry + dt.timedelta(days=day_offset)).strftime(
                "%Y-%m-%d 10:00:00"),
            InvNumber=str(invoice), PolicyNumber=polnum, Category=category,
            Commission=str(commission), Fees=str(fees),
            PolicyClass=policy_class or CLASS_OUT.get(cls.upper(), "BUSINESS"),
        ))
        return invoice

    # 1. Clean renewals: matching class, date inside tolerance -> tier 1.
    for p in policies[:25]:
        emit(p, "RWL", p[6], -3)

    # 2. Transfer renewals -> tier 1, outcome transfer_renewed.
    for p in policies[25:30]:
        emit(p, "TRW", p[6], 2)

    # 3. Lapses: negative income, outcome lapsed_lost, zero renewal income.
    for p in policies[30:35]:
        emit(p, "LAP", -float(p[6]), 5)

    # 4. Renewed policy with an ordinary endorsement on a DIFFERENT invoice.
    #    Must not count as renewal income.
    for p in policies[35:38]:
        emit(p, "RWL", p[6], -1)
        emit(p, "END", 150.00, 10)

    # 5. Renewed policy with an adjustment sharing the renewal's invoice.
    #    Must count as renewal income via the invoice chain.
    for p in policies[38:41]:
        shared = emit(p, "RWL", p[6], -2)
        emit(p, "ADJ", -25.00, 6, invoice=shared)

    # 6. Class conflict: policy number matches but class disagrees -> tier 2,
    #    never tier 1.
    for p in policies[41:44]:
        emit(p, "RWL", p[6], -4, policy_class="MARINEHULL")

    # 7. Date outside tolerance but same financial year -> tier 3.
    for p in policies[44:47]:
        emit(p, "RWL", p[6], 120)

    # 8. Policies 47..52 get no transaction at all -> unmatched once the
    #    renewal window has closed.

    # 9a. Contention that class CAN resolve: distinct PolicyIDs share client and
    #     policy number but differ in class, and the transaction names one of
    #     them. Exactly one policy may be credited.
    for client, polnum, pids, cls, expiry, manager in contended:
        rows.append(row(
            Group1Abbrev=manager, Group2Description="Renewal", Code=client,
            TransactionDate=(expiry + dt.timedelta(days=-2)).strftime("%Y-%m-%d 10:00:00"),
            InvNumber=str(inv), PolicyNumber=polnum, Category="RWL",
            Commission="400.00", Fees="100.00",
            PolicyClass=CLASS_OUT.get((cls or "").upper(), "BUSINESS"),
        ))
        inv += 1

    # 9b. Contention that class CANNOT resolve: the transaction carries a class
    #     with no equivalence mapping, so both twins sit at the same tier. This
    #     is the double-allocation hazard and must go to review with nothing
    #     credited automatically.
    for client, polnum, pids, cls, expiry, manager in contended[:1]:
        rows.append(row(
            Group1Abbrev=manager, Group2Description="Renewal", Code=client,
            TransactionDate=(expiry + dt.timedelta(days=-6)).strftime("%Y-%m-%d 10:00:00"),
            InvNumber=str(inv), PolicyNumber=polnum, Category="TRW",
            Commission="240.00", Fees="60.00",
            PolicyClass="UNMAPPEDCL",
        ))
        inv += 1

    return rows


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    dsn, out = sys.argv[1], sys.argv[2]
    month = dt.date.fromisoformat(
        next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--month=")),
             "2026-08-01"))
    rows = build(dsn, month)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} synthetic transactions for {month} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
