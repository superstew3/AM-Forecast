"""Exclusion engine and transaction classification.

Both load their rules from the reference tables at run time. No manager name,
exclusion string or category code appears as a literal in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .normalise import norm


@dataclass(frozen=True)
class Rule:
    id: int
    target_field: str
    match_type: str
    match_value: str
    rule_name: str


@dataclass(frozen=True)
class ExclusionHit:
    rule_id: int
    field: str
    value: str
    rule_name: str


class ExclusionEngine:
    """Applies configured exclusion rules to a source row.

    A matching record is never dropped. It is imported in full and flagged, with
    the rule, field and value recorded, so it stays available in the audit area
    and can be reversed by deactivating the rule.
    """

    def __init__(self, rules: list[Rule]):
        self._exact = [r for r in rules if r.match_type == "exact"]
        self._contains = [r for r in rules if r.match_type == "contains"]

    @classmethod
    def load(cls, cur, source_type: str) -> "ExclusionEngine":
        cur.execute("""
            SELECT id, target_field, match_type, match_value, rule_name
            FROM exclusion_rule
            WHERE active AND source_type IN (%s, 'both')
            ORDER BY id
        """, (source_type,))
        return cls([Rule(*r) for r in cur.fetchall()])

    def check(self, row: dict) -> ExclusionHit | None:
        """First matching rule wins. Exact rules are evaluated before contains
        rules so the more specific reason is the one recorded."""
        for rule in self._exact:
            raw = row.get(rule.target_field)
            n = norm(raw)
            if n and n == rule.match_value:
                return ExclusionHit(rule.id, rule.target_field, str(raw), rule.rule_name)
        for rule in self._contains:
            raw = row.get(rule.target_field)
            n = norm(raw)
            if n and rule.match_value in n:
                return ExclusionHit(rule.id, rule.target_field, str(raw), rule.rule_name)
        return None


# --- classification ----------------------------------------------------------

# Financial direction is derived from the amount, independently of the category.
# An END row can be a positive or negative endorsement; an RWL row can carry a
# negative correction; an ADJ row can go either way.
_DERIVED = {
    ("RWL", "positive"): "Positive Renewal",
    ("RWL", "negative"): "Renewal Return or Correction",
    ("TRW", "positive"): "Positive Transfer Renewal",
    ("TRW", "negative"): "Transfer Renewal Return or Correction",
    ("N/B", "positive"): "Positive New Business",
    ("N/B", "negative"): "Negative New Business Correction",
    ("NCN", "positive"): "New Business Cancellation",
    ("NCN", "negative"): "New Business Cancellation",
    ("END", "positive"): "Positive Endorsement",
    ("END", "negative"): "Negative Endorsement",
    ("ECN", "positive"): "Endorsement Cancellation",
    ("ECN", "negative"): "Endorsement Cancellation",
    # LAP is always a lapse / end-term cancellation / lost renewal, whichever
    # way the accounting line falls. The Reason field is never used to subdivide
    # or reinterpret it.
    ("LAP", "positive"): "Lapse / Lost Renewal",
    ("LAP", "negative"): "Lapse / Lost Renewal",
    ("MCN", "positive"): "Mid-Term Cancellation",
    ("MCN", "negative"): "Mid-Term Cancellation",
    ("ADJ", "positive"): "Positive Adjustment",
    ("ADJ", "negative"): "Negative Adjustment",
    ("CCN", "positive"): "Policy Reinstatement",
    ("CCN", "negative"): "Policy Reinstatement",
}


def load_category_map(cur) -> dict[str, str]:
    cur.execute("SELECT category, business_classification FROM category_map WHERE active")
    return dict(cur.fetchall())


def direction(amount: Decimal) -> str:
    if amount > 0:
        return "positive"
    if amount < 0:
        return "negative"
    return "nil"


def classify(category: str | None, amount: Decimal,
             category_map: dict[str, str]) -> tuple[str, str, str, bool]:
    """Return (business, derived, direction, is_unmapped).

    An unknown category is never silently assigned. It classifies as 'Unmapped'
    and the caller raises an exception record for the review queue.
    """
    d = direction(amount)
    business = category_map.get(category or "", "Unmapped")
    lookup = d if d != "nil" else "positive"
    derived = _DERIVED.get((category, lookup), "Unmapped")
    return business, derived, d, business == "Unmapped"


def load_alias_map(cur) -> dict[str, str]:
    cur.execute("""SELECT source_manager_norm, canonical_manager
                   FROM manager_alias WHERE active""")
    return dict(cur.fetchall())


def resolve_manager(source_manager: str | None, alias_map: dict[str, str]) -> str | None:
    """Canonical manager, or None when the source manager has no alias row.

    None is a signal, not a default. It raises a missing-mapping exception so an
    unrecognised manager is surfaced rather than absorbed into a total.
    """
    return alias_map.get(norm(source_manager))
