"""Confirmed source reconciliation figures.

One definition, used by the reconciliation screens, the acceptance tests and the
data-quality indicators, so the numbers cannot drift apart between them.
"""
from __future__ import annotations

from decimal import Decimal

TOLERANCE = Decimal("0.01")
CENT = Decimal("0.01")

SALES = {
    "source_rows": 14886,
    "excluded_rows": 2163,
    "included_rows": 12723,
    "positive_income": Decimal("5620647.70"),
    "return_income": Decimal("-659271.01"),
    "net_income": Decimal("4961376.69"),
}

RENEWALS = {
    "source_rows": 6749,
    "unique_policy_ids": 6749,
    "excluded_rows": 975,
    "included_rows": 5774,
    "negative_expected_rows": 3,
    # Twelve, not eleven. PolicyID 931173620 carries Comm 206.73 and CommTax
    # 20.68 exactly offset by Fee -206.73 and FeeTax -20.68. In exact decimal
    # arithmetic that is precisely zero; a floating-point pipeline returns
    # 7.1e-15 and counts the row as non-zero, which is where eleven came from.
    # No monetary total is affected.
    "zero_expected_rows": 12,
    "raw_expected_income": Decimal("3352917.06"),
    "forecast_contribution": Decimal("3354995.38"),
}

ZERO_EXPECTED_EXPLANATION = (
    "12 policies carry exactly zero expected income. The figure is 12, not the 11 "
    "quoted in the original brief: PolicyID 931173620 has commission fully offset "
    "by a negative fee of the same amount, which is exactly zero in decimal "
    "arithmetic but non-zero under floating point. No financial total changes."
)

# The base operating position, asserted on every test run.
BASE_POSITION = {
    "financial_year": 2026,
    "cut_off_date": "2026-07-31",
    "original_renewal_forecast": Decimal("3701892.60"),
    "total_budget": Decimal("3979534.55"),
    "latest_outlook": Decimal("3676619.01"),
    "remaining_budget_gap": Decimal("302915.54"),
}
