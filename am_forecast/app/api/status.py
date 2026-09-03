"""Operational status: what needs attention, in one request.

The routine this system asks of a person is two files a month. Knowing whether
that routine had actually been kept required opening four screens and knowing
what a healthy answer looked like on each -- which is not a routine, it is a
skill, and it decays.

Everything here was already knowable. `actual_load_state()` knew whether a month
was fully imported, `v_original_forecast_month` knew which months had a
forecast, `upload_batch` knew when a file last arrived, `/api/health` knew about
migrations. None of it was gathered anywhere a person would look.

**Nothing in this module decides anything.** Every check but one comes out of
`v_operational_status` with its severity and its wording already attached, for
the same reason financial figures come out of views: a rule restated in a second
place drifts from the first, and the drift is invisible because both sides look
confident.

The exception is the migration check, and it has to be here. It compares the
migration files on disk against what the database has recorded, and only the
running process can see both. `migration_status()` below is the one
implementation of that comparison -- `/api/health` calls it too rather than
keeping its own copy, which is how the two came to disagree about production
before.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from .core import GST_NOTE, current_user, fetch_all, fetch_one, meta

router = APIRouter(prefix="/status", tags=["status"])

# ok < attention < action. Used to roll the rows up into one overall verdict, so
# the sidebar can show a single colour without re-deciding what each row means.
SEVERITY_RANK = {"ok": 0, "attention": 1, "action": 2}

COLUMNS = ("sort_order", "check_key", "title", "severity", "headline", "detail",
           "what_to_do", "as_at", "next_due", "item_count")


def migration_status() -> dict:
    """What this process believes about its own migrations.

    Production sat five migrations behind while every publish reported success,
    and there was no way to ask the running app what it could see; diagnosing it
    came down to inferring from a half-remembered publish timestamp.

    Four facts answer it: whether auto-migrate is on IN THIS PROCESS, how many
    migration files it can find on disk, how many the database has recorded, and
    which files it therefore considers outstanding.

    `database` is included because the two databases are both reached through
    DATABASE_URL, which resolves differently depending on where it is evaluated.
    "Are migrations outstanding" is meaningless without "on which database", and
    that ambiguity has already cost time here.

    Never raises. A status panel that cannot render because the thing it reports
    on is broken is worse than useless.
    """
    try:
        from ..bootstrap import AUTO_MIGRATE, MIGRATIONS, _files
        on_disk = [f.name for f in _files()]
        tracked = fetch_one(
            "SELECT to_regclass('public.schema_migration') IS NOT NULL AS ok")["ok"]
        recorded = fetch_one("SELECT count(*) AS n FROM schema_migration")["n"] \
            if tracked else None
        applied = set()
        if recorded:
            applied = {r["filename"]
                       for r in fetch_all("SELECT filename FROM schema_migration")}
        return {
            "auto_migrate_enabled": AUTO_MIGRATE,
            "path": str(MIGRATIONS),
            "database": fetch_one("SELECT current_database() AS d")["d"],
            "files_found": len(on_disk),
            "recorded_in_database": recorded,
            "outstanding": ([f for f in on_disk if f not in applied]
                            if recorded is not None else on_disk),
        }
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def _migration_row(m: dict) -> dict:
    """The migration check, in the same shape the view produces.

    The only row whose wording is written in Python rather than in SQL, because
    it is the only check the database cannot answer on its own. Kept to one
    function so it is still written once.
    """
    outstanding = m.get("outstanding") or []
    database = m.get("database")
    where = f" on {database}" if database else ""

    if m.get("error"):
        severity = "action"
        headline = "Migration state could not be read"
        detail = (f"{m['error']}. Until this is answerable, nothing on this page "
                  f"can be trusted to reflect the schema actually in use.")
        what = ("Check /api/health, and that the deployment is shipping "
                "migrations/versions.")
    elif not m.get("files_found"):
        severity = "action"
        headline = "No migration files found on disk"
        detail = ("The deployment is not shipping migrations/versions, so this "
                  "process cannot tell whether the schema is current.")
        what = (f"Check that migrations/versions is included in the build at "
                f"{m.get('path')}.")
    elif outstanding:
        severity = "action"
        headline = (f"{len(outstanding)} migration"
                    f"{'' if len(outstanding) == 1 else 's'} outstanding{where}")
        detail = (f"{', '.join(outstanding)}. Migrations do not run on publish -- "
                  f"the schema differ appears to ignore views, and this system is "
                  f"almost entirely views, so a publish reports success while the "
                  f"database silently stays behind. Auto-migrate is "
                  f"{'on' if m.get('auto_migrate_enabled') else 'OFF'} in this "
                  f"process.")
        what = ("Set AM_FORECAST_AUTO_MIGRATE=1 and restart once, then remove it. "
                "Startup DDL is not something to leave on.")
    else:
        severity = "ok"
        headline = f"Schema is current{where}"
        detail = (f"{m['files_found']} migration files on disk, all recorded as "
                  f"applied.")
        what = "Nothing to do."

    return {"sort_order": 9, "check_key": "migrations", "title": "Database schema",
            "severity": severity, "headline": headline, "detail": detail,
            "what_to_do": what, "as_at": None, "next_due": None,
            "item_count": len(outstanding)}


@router.get("")
def operational_status(user=Depends(current_user)):
    """Every check, worst first by severity but stable in the view's own order.

    The two-file routine is the whole interface to this system for most of a
    month. This says whether it has been kept, and what is unavailable if it has
    not.
    """
    try:
        items = fetch_all(f"""
            SELECT {', '.join(COLUMNS)}
            FROM v_operational_status ORDER BY sort_order""")
        view_error = None
    except Exception as exc:
        # The view is itself shipped by a migration. If it is missing, the
        # migration row below is the one that explains why -- returning a 500
        # here would hide the answer behind the symptom.
        items = []
        view_error = f"{type(exc).__name__}: {exc}"

    migrations = migration_status()
    items.append(_migration_row(migrations))

    if view_error:
        items.insert(0, {
            "sort_order": 0, "check_key": "status_view", "title": "Status checks",
            "severity": "action",
            "headline": "The operational checks could not be read",
            "detail": (f"{view_error}. v_operational_status is created by migration "
                       f"0025; if that migration is outstanding below, this is the "
                       f"same fault, not a second one."),
            "what_to_do": "Apply the outstanding migrations, then reload.",
            "as_at": None, "next_due": None, "item_count": 0})

    overall = max((i["severity"] for i in items),
                  key=lambda s: SEVERITY_RANK.get(s, 0), default="ok")

    return {
        "items": items,
        "overall": overall,
        "counts": {s: sum(1 for i in items if i["severity"] == s)
                   for s in ("ok", "attention", "action")},
        "migrations": migrations,
        "meta": meta(notes=[
            "Every date here is the calendar's, in Australia/Melbourne. The "
            "stored cut-off decides nothing and is not read.",
            "Attention means something is due. Action means a figure on a page "
            "is unavailable until it is dealt with.",
        ]),
        "gst_note": GST_NOTE,
    }
