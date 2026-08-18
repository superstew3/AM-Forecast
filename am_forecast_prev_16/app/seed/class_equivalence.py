"""Policy class equivalence.

The two sources do not share a class vocabulary. Renewals Pending uses values
like 'COMMERCIAL MOTOR' and 'HOUSEBOAT INS'; Sales Transactions uses 'COMM MOTOR'
and 'HBOAT'. Only 28 of 89 renewals classes match a sales class as a string, so
class agreement has to be a mapping, not equality.

Each side maps to a canonical class. Two values are *compatible* when they map
to the same canonical class. When either side is unmapped the pair is *unknown*:
that cannot earn the top matching tier, but it does not block a match on client
and policy number, which is stronger evidence than class anyway.

Coverage below is by volume, not by count of distinct values. The long tail of
one-off classes is intentionally left unmapped rather than guessed at; an
administrator adds rows as they appear in the review queue.
"""
from __future__ import annotations

# canonical_class: (renewals values, sales values)
CLASS_GROUPS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "PLEASURE_CRAFT":    (("PLEASURE CRAFT",), ("PLEASURECR", "ZZBOAT")),
    "HOME":              (("HOME INSURANCE",), ("HOME",)),
    "BUSINESS_PACK":     (("BUSINESS PACK", "BUSINESS", "SVU BUSINESS PK"),
                          ("BUSINESS", "ZBUSINESS2", "SVUBUSINES")),
    "PRIVATE_MOTOR":     (("PRIVATE MOTOR",), ("PRIV MOTOR",)),
    "COMMERCIAL_MOTOR":  (("COMMERCIAL MOTOR",), ("COMM MOTOR", "SVUMOTOR")),
    "LANDLORDS":         (("LANDLORDS",), ("LANDLORDS",)),
    "LIABILITY":         (("LIABILITY",), ("LIABILITY", "LIABILIT2")),
    "HOUSEBOAT":         (("HOUSEBOAT INS",), ("HBOAT",)),
    "FARM_PACKAGE":      (("FARM PACKAGE",), ("FARM",)),
    "CARAVAN":           (("CARAVAN",), ("CARAVAN",)),
    "PROF_INDEMNITY":    (("PROF. INDEMNITY", "PI & LIABILITY", "LIABPI"),
                          ("PI", "PI & PL", "LIABPI")),
    "STRATA":            (("STRATA TITLE",), ("STRATA",)),
    "STRATA_COMMERCIAL": (("STRATA COMM",), ("STRATA COM",)),
    "MANAGEMENT":        (("MANAGEMENT",), ("MANAGEMENT", "MANAGEME2")),
    "MOTOR_PRESTIGE":    (("MOTOR PRESTIGE",), ("PRESTIGE M",)),
    "FARM_MOTOR":        (("FARM MOTOR",), ("FARM MOTOR",)),
    "MARINE_CARGO":      (("MARINE CARGO",), ("MARINECARG",)),
    "ISR":               (("I.S.R.",), ("ISR",)),
    "CYBER":             (("CYBER RISKS",), ("CYBER",)),
    "CORPORATE_TRAVEL":  (("CORP TRAVEL",), ("CORP TRAVL",)),
    "MOTORCYCLE":        (("MOTOR BIKE",), ("MOTORCYCLE",)),
    "MARINE_LIABILITY":  (("MAR LIAB",), ("MARINE LIA",)),
    "PAD":               (("PAD",), ("PADLOCK",)),
    "TRANSPACK":         (("TRANSPACK",), ("TRANSPACK",)),
    "CONTRACT_WORKS":    (("CONTRACT WORKS",), ("CONTRACTWK",)),
    "ICT":               (("ICT",), ("INFO TECH",)),
    "GENERAL_PROPERTY":  (("GENERAL PROPERTY",), ("GEN PROP",)),
    "PERSONAL_ACCIDENT": (("PERS ACC/ILLNESS",), ("PERS ACC",)),
    "MOTOR_TRADE":       (("MOTORT",), ("MOTORTRADE",)),
    "MARINE":            (("MARINE",), ("MARINE",)),
    "MARINE_HULL":       (("MARINE H",), ("MARINEHULL",)),
    "MOTOR_FLEET":       (("MOTOR FLEET",), ("MOTORFLEET",)),
    "MOTORHOME":         (("MOTORHOME",), ("MOTORHOME",)),
    "PLANT_MACHINERY":   (("PLANT & MACH",), ("PLANT",)),
    "CAMPERVAN":         (("CAMPERVAN",), ("CAMPER VAN",)),
    "TOOLS":             (("TOOLS",), ("TOOLS",)),
    "TRAVEL":            (("TRAVEL",), ("TRAVEL",)),
    "SHIPREPAIRERS":     (("SHIPREPAIRERS",), ("SHIP REP L",)),
    "VOLUNTARY_WORKER":  (("VOLUNTARY WORKER",), ("VOLUNTARY",)),
    "MEDICAL_MAL":       (("MEDICAL MAL",), ("MED MAL",)),
    "ASSOC_LIABILITY":   (("ASSOC LIAB",), ("ASSOC LIAB",)),
    "MACHINERY_BREAKDOWN": (("MACHINERY B/DOWN",), ("MACHINERY",)),
}


def class_equivalence_rows() -> list[tuple[str, str, str]]:
    """(source_type, source_value, canonical_class), values already uppercased."""
    rows = []
    for canonical, (renewals, sales) in CLASS_GROUPS.items():
        for v in renewals:
            rows.append(("renewals", v.upper(), canonical))
        for v in sales:
            rows.append(("sales", v.upper(), canonical))
    return rows
