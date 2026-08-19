"""Forecast history.

A record of what was forecast for each month, and when. Every accepted Renewals
Pending file adds a row to the manager's timeline, stamped with when it arrived
and who loaded it, so the question "what were we expecting for March, and when
did that change?" has a direct answer.

This replaces the earlier movement analysis, which compared two snapshots and
reported deltas. The deltas are still here, but as the difference between
consecutive rows of a timeline rather than as the primary view — that ordering
matches how the question actually gets asked.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .core import current_financial_year, GST_NOTE, Meta, current_user, fetch_all, fetch_one, meta

router = APIRouter()


class HistoryCell(BaseModel):
    month: dt.date
    value: Decimal | None = None
    change: Decimal | None = None
    is_new: bool = False


class HistoryRow(BaseModel):
    """One forecast as at one point in time."""

    entry_id: str
    kind: str                      # snapshot | prior_year | legacy
    label: str
    source_file: str | None = None
    recorded_at: dt.datetime | None = None
    as_of_date: dt.date | None = None
    recorded_by: str | None = None
    is_current: bool = False
    cells: list[HistoryCell]
    total: Decimal | None = None
    total_change: Decimal | None = None


@router.get("/forecast-history", tags=["forecast"])
def forecast_history(manager: str = Query(...), financial_year: int | None = Query(None),
                     user=Depends(current_user)):
    """The forecast timeline for one manager and one financial year."""
    financial_year = financial_year or current_financial_year()
    who = fetch_one("""SELECT canonical_manager FROM reporting_manager
                       WHERE canonical_manager = %(m)s""", {"m": manager})
    if who is None:
        raise HTTPException(404, f"unknown manager '{manager}'")

    months = [dt.date(financial_year + (7 + i - 1) // 12, (7 + i - 1) % 12 + 1, 1)
              for i in range(12)]
    params = {"m": manager, "fy": financial_year}

    entries: list[dict] = []

    # Baselines that are not snapshots: prior-year actual, legacy dashboard.
    for row in fetch_all("""
            SELECT o.origin, MIN(o.established_at) AS recorded_at,
                   MIN(o.established_by) AS recorded_by,
                   o.forecast_month, SUM(o.forecast_contribution) AS amount
            FROM original_forecast o
            LEFT JOIN v_manager_resolution r ON r.source_manager = o.source_manager
            WHERE o.financial_year = %(fy)s
              AND COALESCE(r.canonical_manager, o.source_manager) = %(m)s
              AND o.grain = 'manager_month'
            GROUP BY o.origin, o.forecast_month""", params):
        key = f"origin:{row['origin']}"
        entry = next((e for e in entries if e["entry_id"] == key), None)
        if entry is None:
            entry = {"entry_id": key, "kind": row["origin"],
                     "label": {"manual_entry": "Supplied forecast figures",
                               "prior_year_actual": "Prior year actual",
                               "legacy_dashboard": "Legacy dashboard figures",
                               "rebaseline": "Rebaselined"}.get(
                                   row["origin"], row["origin"]),
                     "source_file": None, "recorded_at": row["recorded_at"],
                     "as_of_date": None, "recorded_by": row["recorded_by"],
                     "values": {}}
            entries.append(entry)
        entry["values"][row["forecast_month"]] = row["amount"]

    # Every accepted Renewals Pending file, oldest first.
    for row in fetch_all("""
            SELECT s.id AS snapshot_id, s.as_of_date, b.file_name, b.uploaded_by,
                   COALESCE(b.accepted_at, b.uploaded_at) AS recorded_at,
                   p.forecast_month, SUM(p.forecast_contribution) AS amount
            FROM forecast_snapshot s
            JOIN upload_batch b ON b.id = s.batch_id AND b.status = 'accepted'
            JOIN forecast_policy p ON p.snapshot_id = s.id AND NOT p.is_excluded
            LEFT JOIN v_manager_resolution r ON r.source_manager = p.source_manager
            WHERE p.financial_year = %(fy)s
              AND COALESCE(r.canonical_manager, p.source_manager) = %(m)s
            GROUP BY s.id, s.as_of_date, b.file_name, b.uploaded_by, recorded_at,
                     p.forecast_month
            ORDER BY s.id, p.forecast_month""", params):
        key = f"snapshot:{row['snapshot_id']}"
        entry = next((e for e in entries if e["entry_id"] == key), None)
        if entry is None:
            entry = {"entry_id": key, "kind": "snapshot",
                     "label": f"Renewals Pending as at {row['as_of_date']:%d %b %Y}",
                     "source_file": row["file_name"], "recorded_at": row["recorded_at"],
                     "as_of_date": row["as_of_date"], "recorded_by": row["uploaded_by"],
                     "values": {}}
            entries.append(entry)
        entry["values"][row["forecast_month"]] = row["amount"]

    # Baselines first, then snapshots in the order they arrived.
    #
    # Supplied figures cover months no snapshot reaches, so they are the starting
    # position rather than a later revision. Sorting purely by wall-clock time
    # put them last and made them look like the most recent forecast for the
    # whole year, which they are not.
    def order(e):
        floor = dt.datetime.min.replace(tzinfo=dt.timezone.utc)
        return (0 if e["kind"] != "snapshot" else 1, e["recorded_at"] or floor)

    entries.sort(key=order)

    snapshots = [e for e in entries if e["kind"] == "snapshot"]
    current_id = (snapshots[-1]["entry_id"] if snapshots
                  else entries[-1]["entry_id"] if entries else None)

    rows: list[HistoryRow] = []
    previous: dict = {}
    for i, e in enumerate(entries):
        cells, total = [], Decimal(0)
        has_any = False
        for m in months:
            v = e["values"].get(m)
            prior = previous.get(m)
            change = None
            if v is not None:
                total += v
                has_any = True
                if prior is not None and v != prior:
                    change = v - prior
            cells.append(HistoryCell(
                month=m, value=v, change=change,
                is_new=v is not None and prior is None and i > 0))
        prev_total = sum((x for x in previous.values() if x is not None), Decimal(0))
        rows.append(HistoryRow(
            entry_id=e["entry_id"], kind=e["kind"], label=e["label"],
            source_file=e["source_file"], recorded_at=e["recorded_at"],
            as_of_date=e["as_of_date"], recorded_by=e["recorded_by"],
            # The newest snapshot is the live forecast. Where there are no
            # snapshots at all, the last baseline is.
            is_current=(e["entry_id"] == current_id),
            cells=cells, total=total if has_any else None,
            total_change=(total - prev_total) if (i > 0 and previous) else None))
        # Later entries only override the months they actually cover, so a
        # narrower file does not read as everything else vanishing.
        previous = {**previous, **{m: v for m, v in e["values"].items()}}

    return {
        "canonical_manager": manager,
        "financial_year": financial_year,
        "financial_year_label": f"FY{financial_year}-{str(financial_year + 1)[2:]}",
        "months": months,
        "entries": [r.model_dump() for r in rows],
        "entry_count": len(rows),
        "meta": meta(financial_year, notes=[
            "Each row is one forecast as at a point in time. A later row only "
            "changes the months it covers, so a narrower export does not read as "
            "everything else disappearing."]),
        "gst_note": GST_NOTE,
    }
