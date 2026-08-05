#!/usr/bin/env python3
"""Render a static preview of the dashboard from live API responses.

Not a mock. Every figure below is fetched from the running application through
the same endpoints the React app calls, and formatted with the same rules,
including N/A handling. It exists so the interface can be reviewed without
standing up Node and a browser.

    python scripts/build_preview.py <dsn> <out.html>
"""
from __future__ import annotations

import datetime as dt
import html
import os
import sys
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CENT = Decimal("0.01")
NA_HTML = '<span class="na">N/A<span class="na-mark">?</span></span>'


def money(m, *, raw=False) -> str:
    if raw:
        m = {"value": m, "available": m is not None}
    if not m or not m.get("available") or m.get("value") is None:
        reason = (m or {}).get("reason") or "Not available"
        return f'<span class="na" title="{html.escape(reason)}">N/A<span class="na-mark">?</span></span>'
    v = Decimal(str(m["value"])).quantize(CENT, rounding=ROUND_HALF_UP)
    text = f"{abs(v):,.2f}"
    return (f'<span class="val">(${text})</span>' if v < 0
            else f'<span class="val">${text}</span>')


def pct(m, digits=1) -> str:
    if not m or not m.get("available") or m.get("value") is None:
        reason = (m or {}).get("reason") or "Not available"
        return f'<span class="na" title="{html.escape(reason)}">N/A<span class="na-mark">?</span></span>'
    return f'<span class="val">{Decimal(str(m["value"])) * 100:.{digits}f}%</span>'


def esc(v) -> str:
    return NA_HTML if v is None else html.escape(str(v))


def metric(label, value_html, sub="", emphasis=False, hint="") -> str:
    h = f'<span class="hint" title="{html.escape(hint)}">i</span>' if hint else ""
    return (f'<div class="metric{" metric-emphasis" if emphasis else ""}">'
            f'<div class="metric-label">{html.escape(label)}{h}</div>'
            f'<div class="metric-value">{value_html}</div>'
            f'{f"<div class=\'metric-sub\'>{sub}</div>" if sub else ""}</div>')


def table(columns, rows, caption="") -> str:
    if not rows:
        return f'<div class="state empty">No {caption} for the current filters.</div>'
    head = "".join(
        f'<th class="{"right" if c.get("right") else ""}">{html.escape(c["label"])}'
        f'{f"<span class=\'hint\' title=\'{html.escape(c[chr(104)+chr(105)+chr(110)+chr(116)])}\'>i</span>" if c.get("hint") else ""}</th>'
        for c in columns)
    body = ""
    for r in rows:
        cells = "".join(
            f'<td class="{"right" if c.get("right") else ""}">{c["render"](r)}</td>'
            for c in columns)
        body += f"<tr>{cells}</tr>"
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
            f"<tbody>{body}</tbody></table></div>")


def panel(title, subtitle, inner) -> str:
    sub = f'<p class="subtitle">{html.escape(subtitle)}</p>' if subtitle else ""
    return (f'<section class="panel"><header class="panel-head"><div>'
            f'<h2>{html.escape(title)}</h2>{sub}</div></header>{inner}</section>')


def build(client) -> str:
    biz = client.get("/api/business?financial_year=2026").json()
    managers = client.get("/api/managers?period=quarter&financial_year=2026").json()
    movement = client.get("/api/forecast-movement").json()
    returns = client.get("/api/return-income").json()
    policies = client.get("/api/policies?limit=8").json()
    review = client.get("/api/review").json()
    dq = client.get("/api/data-quality").json()
    budget = client.get("/api/budget?financial_year=2026").json()
    uploads = client.get("/api/uploads?limit=6").json()
    base = client.get("/api/base-position").json()

    css = (ROOT / "web" / "src" / "styles.css").read_text()

    # --- business ------------------------------------------------------------
    business_html = panel(
        "Position against budget",
        "Budget is the Original Renewal Forecast plus the new business growth "
        "target. It does not move when the Latest Forecast moves.",
        '<div class="metric-grid">'
        + metric("Net Actual Income", money(biz["net_actual_income"]), emphasis=True,
                 hint="Positive income plus signed return income. Includes returns.")
        + metric("Total Budget", money(biz["total_budget"]),
                 sub=f'Achievement {pct(biz["budget_achievement"])}',
                 hint="Original Renewal Forecast + New Business Growth Target.")
        + metric("Latest Outlook", money(biz["latest_outlook"]), emphasis=True,
                 hint="Completed-period actuals plus Latest Forecast for future periods. "
                      "Contains no assumed future new business.")
        + metric("Remaining Budget Gap", money(biz["remaining_budget_gap"]),
                 hint="Income still to be found through new business, retention or "
                      "other actual activity.")
        + "</div>")

    business_html += panel(
        "Actual income", "From Sales Transactions only.",
        '<div class="metric-grid">'
        + metric("Positive Actual Income", money(biz["positive_actual_income"]))
        + metric("Return Income", money(biz["return_income"]),
                 hint="Absolute value of negative transactions.")
        + metric("Net Actual Income", money(biz["net_actual_income"]))
        + metric("Actual New Business", money(biz["actual_new_business"]),
                 hint="Recognised only once it appears in Sales Transactions.")
        + "</div>")

    business_html += panel(
        "Renewal forecast",
        "Original is frozen at baseline. Latest reflects the newest accepted "
        "snapshot for future months.",
        '<div class="metric-grid">'
        + metric("Original Renewal Forecast", money(biz["original_renewal_forecast"]))
        + metric("Latest Renewal Forecast", money(biz["latest_renewal_forecast"]),
                 hint="A completed month has no Latest Forecast; it reports actuals.")
        + metric("Forecast Movement", money(biz["forecast_movement"]))
        + "</div>")

    business_html += panel(
        "Where income was returned",
        "Each category is reported separately rather than as one lump of leakage.",
        '<div class="metric-grid">'
        + metric("Lapse / Lost Renewal", money(biz["lapse_return_income"]))
        + metric("Mid-Term Cancellation", money(biz["midterm_cancellation_return_income"]))
        + metric("New Business Cancellation",
                 money(biz["new_business_cancellation_return_income"]))
        + metric("Negative Endorsements", money(biz["negative_endorsements"]))
        + metric("Endorsement Cancellations", money(biz["endorsement_cancellations"]))
        + "</div>")

    # --- managers ------------------------------------------------------------
    mgr_cols = [
        {"label": "Manager", "render": lambda r: html.escape(r["canonical_manager"])},
        {"label": "Qtr", "render": lambda r: f'Q{r["financial_quarter"]}'},
        {"label": "Original Forecast", "right": 1,
         "render": lambda r: money(r["original_forecast"])},
        {"label": "Latest Forecast", "right": 1,
         "hint": "N/A for completed months, which report actuals.",
         "render": lambda r: money(r["latest_forecast"])},
        {"label": "Net Actual", "right": 1,
         "render": lambda r: money(r["net_actual_income"])},
        {"label": "Return Income", "right": 1,
         "render": lambda r: money(r["return_income"])},
        {"label": "NB Target", "right": 1,
         "render": lambda r: money(r["new_business_growth_target"])},
        {"label": "Total Budget", "right": 1,
         "render": lambda r: money(r["total_budget"])},
        {"label": "Variance", "right": 1,
         "render": lambda r: money(r["budget_variance"])},
        {"label": "Budget %", "right": 1,
         "render": lambda r: pct(r["budget_achievement"])},
        {"label": "Renewal %", "right": 1,
         "hint": "N/A where no usable baseline exists.",
         "render": lambda r: pct(r["renewal_achievement"])},
        {"label": "Outlook", "right": 1,
         "render": lambda r: money(r["latest_outlook"])},
        {"label": "Gap", "right": 1,
         "render": lambda r: money(r["remaining_budget_gap"])},
    ]
    q1 = [r for r in managers["items"] if r["financial_quarter"] == 1]
    managers_html = panel(
        "Account manager performance, FY2026-27 Q1",
        "Inactive, legacy and unmapped managers are out of rankings by default. "
        "Their actual income still counts towards business totals.",
        table(mgr_cols, q1, "manager performance"))

    # --- movement ------------------------------------------------------------
    t = movement["totals"]
    movement_html = panel(
        "Forecast movement, Original to Latest",
        "A removed policy reduces Latest Forecast and is reported here. It never "
        "creates negative forecast income.",
        '<div class="metric-grid">'
        + metric("Policies removed", f'<span class="val">{t["policies_removed"]}</span>',
                 sub=f'{money(t["income_removed"], raw=True)} removed')
        + metric("Policies added", f'<span class="val">{t["policies_added"]}</span>',
                 sub=f'{money(t["income_added"], raw=True)} added')
        + metric("Amount changes", money(t["amount_changes"], raw=True))
        + metric("Manager transfers", f'<span class="val">{t["manager_transfers"]}</span>',
                 hint="Counted from the independent manager-change flag, so a policy "
                      "that also changed amount is still counted here.")
        + metric("Detail changes", f'<span class="val">{t["detail_changes"]}</span>')
        + metric("Several changes at once",
                 f'<span class="val">{t["multi_attribute_changes"]}</span>')
        + metric("Net forecast movement",
                 money(t["net_forecast_movement"], raw=True), emphasis=True)
        + "</div>")

    # --- returns -------------------------------------------------------------
    returns_html = panel(
        "Return income", "Signed amounts reduce Net Actual Income; absolute amounts "
        "show the size of the leakage.",
        table([
            {"label": "Classification",
             "render": lambda r: html.escape(r["derived_classification"])},
            {"label": "Signed", "right": 1,
             "render": lambda r: money(r["signed_return_income"], raw=True)},
            {"label": "Absolute", "right": 1,
             "render": lambda r: money(r["absolute_return_income"], raw=True)},
            {"label": "Transactions", "right": 1,
             "render": lambda r: f'<span class="val">{r["transaction_rows"]}</span>'},
        ], returns["items"], "return income"))

    # --- policies ------------------------------------------------------------
    policies_html = panel(
        f'Policy-level renewals ({policies["total"]:,} forecast policies)',
        "Renewal income is RWL and TRW only. Total associated income includes every "
        "line attached to the policy and answers a different question.",
        table([
            {"label": "PolicyID", "render": lambda r: esc(r["policy_id"])},
            {"label": "Client", "render": lambda r: esc(r["client_code"])},
            {"label": "Policy number", "render": lambda r: esc(r["policy_number"])},
            {"label": "Class", "render": lambda r: esc(r["class_abbrev"])},
            {"label": "Expiry", "render": lambda r: esc(r["expiry_date"])},
            {"label": "Manager", "render": lambda r: esc(r["canonical_manager"])},
            {"label": "Original", "right": 1,
             "render": lambda r: money(r["original_forecast_income"], raw=True)},
            {"label": "Latest", "right": 1,
             "render": lambda r: money(r["latest_forecast_income"], raw=True)},
            {"label": "Renewal income", "right": 1,
             "render": lambda r: money(r["renewal_transaction_income"], raw=True)},
            {"label": "Total associated", "right": 1,
             "render": lambda r: money(r["total_associated_income"], raw=True)},
            {"label": "Outcome", "render": lambda r:
             f'<span class="chip outcome-{r["outcome"]}">{html.escape(r["outcome"])}</span>'},
            {"label": "Tier", "right": 1, "render": lambda r: esc(r["best_tier"])},
        ], policies["items"], "policies"))

    # --- review --------------------------------------------------------------
    c = review["counts"]
    review_html = panel(
        "Matching review queue",
        "Only the first group needs individual decisions. The other two are bulk "
        "artefacts with a known cause, separated so they cannot bury real exceptions.",
        '<div class="metric-grid">'
        + metric("Needs a decision", f'<span class="val">{c["actionable"]}</span>',
                 sub=html.escape(review["explanations"]["actionable"]), emphasis=True)
        + metric("July timing artefacts",
                 f'<span class="val">{c["july_timing_artefacts"]}</span>',
                 sub=html.escape(review["explanations"]["july_timing_artefacts"]))
        + metric("Outside matching scope",
                 f'<span class="val">{c["out_of_scope"]}</span>',
                 sub=html.escape(review["explanations"]["out_of_scope"]))
        + "</div>")

    # --- data quality --------------------------------------------------------
    indicators = [
        ("negative_expected_policies", "Negative expected income"),
        ("zero_expected_policies", "Zero expected income"),
        ("overdue_pending_policies", "Overdue pending"),
        ("residual_pending_policies", "Residual pending"),
        ("unmapped_managers", "Unmapped managers"),
        ("unmapped_categories", "Unmapped categories"),
        ("unmapped_class_equivalences", "Unmapped class equivalences"),
        ("restated_transactions", "Restated transactions"),
        ("ambiguous_matches", "Ambiguous matches"),
        ("allocation_breaches", "Allocation breaches"),
        ("unavailable_baselines", "Unavailable baselines"),
        ("partial_financial_years", "Partial financial years"),
        ("excluded_sales_records", "Highview-excluded transactions"),
        ("excluded_forecast_records", "Highview-excluded policies"),
    ]
    cards = ""
    for key, label in indicators:
        value = dq["counts"][key]
        expected = dq["expected"].get(key)
        exp = (f'<span class="indicator-expected">expected {expected} '
               f'{"&#10003;" if expected == value else "&mdash; mismatch"}</span>'
               if expected is not None else "")
        cards += (f'<div class="indicator drillable"><span class="indicator-value">{value}'
                  f'</span><span class="indicator-label">{html.escape(label)}</span>{exp}</div>')
    dq_html = panel("Data quality and reconciliation",
                    "Every indicator with a drill-down opens the underlying records.",
                    f'<div class="indicator-grid">{cards}</div>'
                    f'<p class="footnote">{html.escape(dq["notes"]["zero_expected_policies"])}</p>')

    baseline_rows = [b for b in dq["baselines"] if b["forecast_month"] >= "2026-07-01"][:4]
    dq_html += panel(
        "Forecast baselines",
        "A month that is not complete reports N/A rather than zero, and listed "
        "managers report N/A even where the month itself is usable.",
        table([
            {"label": "Month", "render": lambda r: esc(r["forecast_month"])},
            {"label": "Status", "render": lambda r: esc(r["baseline_status"])},
            {"label": "Source", "render": lambda r: esc(r["baseline_source"])},
            {"label": "Manager exceptions", "render": lambda r:
             html.escape(", ".join(r["manager_exceptions"])) if r["manager_exceptions"] else "&mdash;"},
        ], baseline_rows, "baselines"))

    # --- budget --------------------------------------------------------------
    budget_rows = [r for r in budget["quarters"] if r["financial_quarter"] == 1][:8]
    budget_html = panel(
        "Budget, FY2026-27 Q1",
        "The active assumption and the level of the hierarchy that supplied it are "
        "shown on every row.",
        table([
            {"label": "Manager", "render": lambda r: esc(r["canonical_manager"])},
            {"label": "Original Forecast", "right": 1,
             "render": lambda r: money(r["original_renewal_forecast"], raw=True)},
            {"label": "Assumption from", "render": lambda r:
             f'<span class="chip">{html.escape(r["growth_basis"])}</span>'},
            {"label": "Growth %", "right": 1,
             "render": lambda r: pct({"value": r["growth_pct"], "available": r["growth_pct"] is not None})},
            {"label": "Dollar override", "right": 1,
             "render": lambda r: money({"value": r["dollar_override"],
                                        "available": r["dollar_override"] is not None,
                                        "reason": "No dollar override; the percentage is active."})},
            {"label": "NB target", "right": 1,
             "render": lambda r: money(r["new_business_growth_target"], raw=True)},
            {"label": "Total Budget", "right": 1,
             "render": lambda r: money(r["total_budget"], raw=True)},
        ], budget_rows, "budget"))

    # --- uploads -------------------------------------------------------------
    uploads_html = panel(
        "Uploads and audit history",
        "Prepare stages and previews a file without touching any reported figure. "
        "The preview shows exactly what will land on accept.",
        table([
            {"label": "Batch", "render": lambda r: esc(r["id"])},
            {"label": "File", "render": lambda r: esc(r["file_name"])},
            {"label": "Type", "render": lambda r: esc(r["file_type"])},
            {"label": "Hash", "render": lambda r: f'<code>{r["file_sha256"][:12]}</code>'},
            {"label": "Status", "render": lambda r:
             f'<span class="chip status-{r["status"]}">{r["status"]}</span>'},
            {"label": "Source rows", "right": 1, "render": lambda r: esc(r["source_row_count"])},
            {"label": "Accepted", "right": 1, "render": lambda r: esc(r["accepted_row_count"])},
            {"label": "Duplicates", "right": 1, "render": lambda r: esc(r["duplicate_row_count"])},
            {"label": "Excluded", "right": 1, "render": lambda r: esc(r["excluded_row_count"])},
            {"label": "Net income", "right": 1,
             "render": lambda r: money(r["net_income"], raw=True)},
            {"label": "Uploaded by", "render": lambda r: esc(r["uploaded_by"])},
        ], uploads["items"], "batches"))

    nav = "".join(
        f'<a href="#{k}" class="{"active" if k == "business" else ""}">{v}</a>'
        for k, v in [("business", "Business performance"),
                     ("managers", "Account managers"),
                     ("movement", "Forecast movement"),
                     ("returns", "Return income"),
                     ("policies", "Policy renewals"),
                     ("review", "Matching review"),
                     ("budget", "Budget"),
                     ("dq", "Data quality"),
                     ("uploads", "Uploads & audit")])

    banner = (f'<div class="gst-banner"><strong>All income figures are GST inclusive.'
              f'</strong><span class="gst-meta">Reporting cut-off '
              f'{biz["meta"]["cut_off_date"]} &middot; Australia/Melbourne</span></div>')
    notes = ("<ul class='notes'>"
             + "".join(f"<li>{html.escape(n)}</li>" for n in biz["meta"]["notes"])
             + "</ul>")
    warning = (
        '<div class="warning"><strong>July 2026 uses a mixed baseline.</strong> '
        'Original Forecast comes from the Legacy Dashboard Forecast at manager-month '
        'level, not policy level. Actuals come from Sales Transactions. The two '
        'residual pending policies belong to Latest Forecast only. Policy-level '
        'renewal achievement is reliable from August 2026 onward. No baseline is '
        'available for Cameron Stewart, Dinghy Scheme and Anastasia K, which show N/A.'
        "</div>")
    base_note = ("" if base["is_base_state"] else
                 '<div class="warning small">The database is not in the clean base '
                 "state.</div>")

    sections = [
        ("business", "Overall business performance", "FY2026-27",
         banner + notes + warning + business_html),
        ("managers", "Account manager performance", "FY2026-27", banner + managers_html),
        ("movement", "Forecast movement", "Original to Latest", banner + movement_html),
        ("returns", "Return income", "", banner + returns_html),
        ("policies", "Policy-level renewals", "", banner + policies_html),
        ("review", "Matching review queue", "", banner + review_html),
        ("budget", "Budget", "FY2026-27", banner + budget_html),
        ("dq", "Data quality", "", banner + dq_html),
        ("uploads", "Uploads and audit history", "", banner + uploads_html),
    ]
    body = "".join(
        f'<section id="{sid}" class="page"><h1>{html.escape(title)}'
        f'{f" <span class=\'fy\'>{fy}</span>" if fy else ""}</h1>{inner}</section>'
        for sid, title, fy, inner in sections)

    return f"""<!doctype html>
<html lang="en-AU"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Account Manager Income Forecasting — dashboard preview</title>
<style>{css}
.page {{ display: none; }}
.page:target {{ display: block; }}
#business {{ display: block; }}
body:has(.page:target) #business {{ display: none; }}
#business:target {{ display: block !important; }}
.preview-note {{ background:#101b25; color:#c9d4de; padding:10px 30px; font-size:12px; }}
</style></head>
<body>
<div class="shell">
  <aside>
    <div class="brand"><span class="brand-mark">AM</span>
      <div><strong>Income Forecasting</strong><small>Performance &amp; budget</small></div>
    </div>
    <nav>{nav}</nav>
    <div class="identity">
      <label>Role<select disabled><option>Viewer</option></select></label>
      <small>Static preview &middot; live data</small>
    </div>
    {base_note}
  </aside>
  <main>
    <div class="preview-note">
      Static preview rendered from live API responses on
      {dt.datetime.now():%d %b %Y %H:%M}. Use the navigation to move between areas.
    </div>
    {body}
  </main>
</div>
</body></html>"""


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    os.environ["AM_FORECAST_DSN"] = sys.argv[1]
    from fastapi.testclient import TestClient

    from app.api import app
    with TestClient(app) as client:
        html_out = build(client)
    Path(sys.argv[2]).write_text(html_out)
    print(f"wrote {len(html_out):,} bytes to {sys.argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
