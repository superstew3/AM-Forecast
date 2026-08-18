"""Application entry point."""
from __future__ import annotations

from decimal import Decimal

import os
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from ..validation import INCOME_BASIS, TOLERANCE as CENT
from .core import (
    DSN,
    GST_NOTE, TIMEZONE, User, current_user, fetch_all, fetch_one, meta, to_cents,
)
from .analytics import router as analytics_router
from .auth import router as auth_router
from .bonus import router as bonus_router
from .forecast_history import router as forecast_history_router
from .manager_detail import router as manager_detail_router
from .forecast_months import router as forecast_months_router
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
app.include_router(forecast_months_router, prefix="/api")


@app.on_event("startup")
def _bootstrap() -> None:
    """Bring the database up to date, when explicitly asked to.

    Disabled unless AM_FORECAST_AUTO_MIGRATE=1. It exists for deployments where
    the platform ships new code but never applies the matching schema, which
    presents as an app that loads and then fails on every query.
    """
    import logging

    from ..bootstrap import run
    try:
        result = run(DSN)
    except Exception:
        logging.exception("bootstrap failed; the app will start but may not work")
        return
    if result["migrated"]:
        logging.warning("applied migrations at startup: %s",
                        ", ".join(result["migrated"]))
    if result["users_created"]:
        logging.warning("created initial accounts: %s",
                        ", ".join(result["users_created"]))
    # Say why nothing happened. An empty result can mean "already done", "no
    # passwords supplied" or "wrong database", and those need different fixes.
    for note in result.get("notes", []):
        logging.warning("account seeding: %s", note)


@app.get("/api/health", tags=["system"])
def health():
    """Unauthenticated on purpose, so a monitor can poll it.

    Reports schema readiness as well as liveness. An app serving pages against
    a database that never received its migrations looks healthy by any simpler
    check, and that is precisely the failure worth catching.
    """
    checks = {}
    ready = True
    cut_off = None
    try:
        row = fetch_one("SELECT cut_off_date FROM reporting_settings WHERE id = 1")
        checks["database"] = "ok"
        if row is None:
            # Schema present, reference data absent. Naming that specifically
            # saves someone hunting through application code for a fault that
            # is really an unfinished setup.
            checks["reference data"] = "MISSING — run app.seed.load_seed"
            ready = False
        else:
            cut_off = row["cut_off_date"]
    except Exception as exc:
        checks["database"] = f"unavailable: {type(exc).__name__}"
        return {"status": "unhealthy", "ready": False, "checks": checks,
                "hint": "The schema is missing or unreachable. Check the "
                        "connection string, and that migrations have been "
                        "applied to THIS database."}

    for table, label in (("app_user", "accounts"), ("user_session", "sessions"),
                         ("auth_event", "auth audit"),
                         ("sales_transaction", "transactions")):
        present = fetch_one("SELECT to_regclass(%(t)s) IS NOT NULL AS ok",
                            {"t": f"public.{table}"})["ok"]
        checks[label] = "ok" if present else "MISSING — migrations not applied"
        ready = ready and present

    if checks.get("accounts") == "ok":
        n = fetch_one("SELECT count(*) AS n FROM app_user WHERE email IS NOT NULL")["n"]
        checks["accounts"] = f"{n} account(s)" if n else "NONE — nobody can sign in"
        ready = ready and n > 0

    return {"status": "ok" if ready else "not ready", "ready": ready,
            "checks": checks, "cut_off_date": cut_off, "timezone": TIMEZONE,
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
    fy = fetch_one("""SELECT au_financial_year(cut_off_date) AS fy
                      FROM reporting_settings WHERE id = 1""")["fy"]
    live = fetch_one("""
        SELECT (SELECT SUM(forecast_contribution) FROM original_forecast
                 WHERE financial_year = %(fy)s) AS original_renewal_forecast,
               (SELECT SUM(total_budget) FROM v_budget_quarter
                 WHERE financial_year = %(fy)s) AS total_budget,
               (SELECT SUM(latest_outlook) FROM v_outlook_quarter
                 WHERE financial_year = %(fy)s) AS latest_outlook,
               (SELECT SUM(remaining_budget_gap) FROM v_outlook_quarter
                 WHERE financial_year = %(fy)s) AS remaining_budget_gap,
               (SELECT cut_off_date FROM reporting_settings WHERE id = 1) AS cut_off_date,
               (SELECT count(*) FROM forecast_snapshot) AS snapshots,
               (SELECT count(*) FROM sales_transaction) AS transactions,
               (SELECT count(*) FROM sales_transaction
                 WHERE fingerprint LIKE 'pytest-%%' OR fingerprint LIKE 'synthetic-%%')
                 AS synthetic_transactions
    """, {"fy": fy})

    def cents(value) -> str | None:
        # Budget derives from a percentage and legitimately carries sub-cent
        # precision, so the comparison is made at cent resolution.
        result = to_cents(value)
        return None if result is None else str(result)

    forecast = live["original_renewal_forecast"] or 0
    budget = live["total_budget"] or 0
    outlook = live["latest_outlook"] or 0
    gap = live["remaining_budget_gap"] or 0

    # Internal consistency rather than four fixed figures.
    #
    # The previous version asserted the exact position of one dataset. That
    # caught drift, but every figure became wrong the moment a new export was
    # loaded, and the check had to be rewritten by hand each time — which meant
    # it was measuring the dataset, not the system. What actually needs to hold
    # is the relationship between the figures, and that survives new data.
    checks = {
        "budget_at_or_above_forecast": budget >= forecast,
        "gap_reconciles_to_budget_less_outlook":
            abs((budget - outlook) - gap) <= CENT,
        "forecast_present": forecast > 0,
        "outlook_present": outlook > 0,
        "no_synthetic_transactions": live["synthetic_transactions"] == 0,
        # A cut-off must not claim a month is complete when the data for it
        # stops part-way through.
        #
        # This used to test whether transactions continued into a LATER month,
        # as a proxy for the cut-off month being fully covered. The proxy breaks
        # on the commonest case there is: an export ending exactly at month end.
        # Sales running to 31 July with a 31 July cut-off gave "July is not
        # complete", which is the one arrangement that is unambiguously correct,
        # and it failed the whole base-state flag on a healthy database.
        #
        # Coverage is now read from what the sales imports actually span, which
        # is recorded per batch and needs no proxy. A month counts as complete
        # only when every day of it has been imported -- so a file ending on the
        # 11th still correctly reports its month incomplete.
        "cut_off_month_is_complete": fetch_one("""
            SELECT actual_load_state((SELECT date_trunc('month', cut_off_date)::date
                                      FROM reporting_settings WHERE id = 1)) = 'full'
                   OR NOT EXISTS (SELECT 1 FROM sales_transaction WHERE NOT is_excluded)
                   AS ok""")["ok"],
    }
    return {"live": {**live,
                     "financial_year": fy,
                     "rounded": {k: cents(live[k]) for k in
                                 ("original_renewal_forecast", "total_budget",
                                  "latest_outlook", "remaining_budget_gap")}},
            "checks": checks,
            "is_base_state": all(checks.values()),
            "income_basis": INCOME_BASIS,
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
