"""Application entry point."""
from __future__ import annotations

from decimal import Decimal

import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from ..validation import BASE_POSITION, TOLERANCE as CENT
from .core import (
    GST_NOTE, TIMEZONE, User, current_user, fetch_all, fetch_one, meta, to_cents,
)
from .analytics import router as analytics_router
from .auth import router as auth_router
from .bonus import router as bonus_router
from .forecast_history import router as forecast_history_router
from .manager_detail import router as manager_detail_router
from .operations import router as operations_router
from .settings import router as settings_router
from .reporting import router as reporting_router

app = FastAPI(
    title="Account Manager Income Forecasting",
    version="1.0.0",
    description=(
        "Income forecasting, budgeting and performance reporting.\n\n"
        "Every financial figure is served from a database view. The API does not "
        "recompute income, forecasts, budgets, outlook, outcomes or achievement.\n\n"
        f"{GST_NOTE}"),
)
# Credentialed requests cannot use a wildcard origin, and should not: the
# session cookie must only travel to origins we name.
CORS_ORIGINS = [o for o in os.environ.get(
    "AM_FORECAST_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
).split(",") if o]

app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router, prefix="/api")
app.include_router(reporting_router, prefix="/api")
app.include_router(manager_detail_router, prefix="/api")
app.include_router(analytics_router, prefix="/api")
app.include_router(settings_router, prefix="/api")
app.include_router(forecast_history_router, prefix="/api")
app.include_router(bonus_router, prefix="/api")
app.include_router(operations_router, prefix="/api")


@app.get("/api/health", tags=["system"])
def health():
    """Unauthenticated on purpose, so a monitor can poll it."""
    row = fetch_one("SELECT cut_off_date FROM reporting_settings WHERE id = 1")
    return {"status": "ok", "cut_off_date": row["cut_off_date"], "timezone": TIMEZONE,
            "gst_note": GST_NOTE}


@app.get("/api/session", tags=["system"])
def session(user: User = Depends(current_user)):
    """Who the caller is and what they may do."""
    return {
        "username": user.username,
        "role": user.role,
        "can": {
            "view": True,
            "export": True,
            "drill_down": user.at_least("manager"),
            "upload": user.at_least("administrator"),
            "accept_reject_rollback": user.at_least("administrator"),
            "maintain_mappings": user.at_least("administrator"),
            "adjust_budget": user.at_least("administrator"),
            "resolve_matching": user.at_least("administrator"),
            "rebaseline": user.at_least("administrator"),
        },
    }


@app.get("/api/reference", tags=["system"])
def reference(user: User = Depends(current_user)):
    """Filter options, sourced from the data rather than hardcoded."""
    return {
        "managers": fetch_all("""SELECT canonical_manager, status, include_in_rankings
                                 FROM reporting_manager
                                 ORDER BY display_order NULLS LAST, canonical_manager"""),
        "financial_years": fetch_all("""SELECT DISTINCT financial_year, data_domain,
                                               coverage_status
                                        FROM period_coverage ORDER BY financial_year"""),
        "policy_classes": fetch_all("""SELECT DISTINCT class_abbrev AS value
                                       FROM forecast_policy WHERE NOT is_excluded
                                       ORDER BY 1"""),
        "underwriters": fetch_all("""SELECT DISTINCT underwriter_abbrev AS value
                                     FROM forecast_policy WHERE NOT is_excluded
                                       AND underwriter_abbrev IS NOT NULL ORDER BY 1"""),
        "categories": fetch_all("""SELECT category, business_classification
                                   FROM category_map WHERE active ORDER BY category"""),
        "outcomes": fetch_all("""SELECT DISTINCT outcome AS value FROM policy_outcome
                                 ORDER BY 1"""),
        "meta": meta(),
    }


@app.get("/api/base-position", tags=["system"])
def base_position(user: User = Depends(current_user)):
    """The expected clean base operating position, and the live figures beside it.

    Exposed so the interface can show, and tests can assert, that the production
    position is the supplied base state and not a leftover test scenario.
    """
    live = fetch_one("""
        SELECT (SELECT SUM(forecast_contribution) FROM original_forecast
                 WHERE financial_year = 2026) AS original_renewal_forecast,
               (SELECT SUM(total_budget) FROM v_budget_quarter
                 WHERE financial_year = 2026) AS total_budget,
               (SELECT SUM(latest_outlook) FROM v_outlook_quarter
                 WHERE financial_year = 2026) AS latest_outlook,
               (SELECT SUM(remaining_budget_gap) FROM v_outlook_quarter
                 WHERE financial_year = 2026) AS remaining_budget_gap,
               (SELECT cut_off_date FROM reporting_settings WHERE id = 1) AS cut_off_date,
               (SELECT count(*) FROM forecast_snapshot) AS snapshots,
               (SELECT count(*) FROM sales_transaction) AS transactions
    """)
    expected = {k: (str(v) if not isinstance(v, (int, str)) else v)
                for k, v in BASE_POSITION.items()}

    def cents(value) -> str | None:
        # Budget derives from a percentage and legitimately carries sub-cent
        # precision, so the comparison is made at cent resolution rather than on
        # the raw value.
        result = to_cents(value)
        return None if result is None else str(result)

    checks = {
        "original_renewal_forecast":
            cents(live["original_renewal_forecast"]) == expected["original_renewal_forecast"],
        "total_budget": cents(live["total_budget"]) == expected["total_budget"],
        "latest_outlook": cents(live["latest_outlook"]) == expected["latest_outlook"],
        "remaining_budget_gap":
            cents(live["remaining_budget_gap"]) == expected["remaining_budget_gap"],
        "cut_off_date": str(live["cut_off_date"]) == expected["cut_off_date"],
        "single_snapshot": live["snapshots"] == 1,
        "no_synthetic_transactions": live["transactions"] == 14886,
    }
    return {"expected": expected,
            "live": {**live, "rounded": {k: cents(live[k]) for k in
                                         ("original_renewal_forecast", "total_budget",
                                          "latest_outlook", "remaining_budget_gap")}},
            "checks": checks,
            "is_base_state": all(checks.values()),
            "gst_note": GST_NOTE}


# --- static frontend ---------------------------------------------------------
# Serving the built SPA from the API keeps deployment to a single process. In
# development the Vite dev server proxies /api here instead.
_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        """Client-side routing: any non-API path returns the app shell."""
        return FileResponse(_DIST / "index.html")
