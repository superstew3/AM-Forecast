#!/usr/bin/env python3
"""Render a static preview of the dashboard from live API responses.

Not a mock. Every figure below is fetched from the running application through
the same endpoints the React app calls, and formatted with the same rules,
including N/A handling. It exists so the interface can be reviewed without
standing up Node and a browser.

    python scripts/build_preview.py <dsn> <out.html>
"""
from __future__ import annotations

import base64
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
    # Negatives red as well as bracketed, exactly as the application renders them.
    return (f'<span class="val negative">(${text})</span>' if v < 0
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


def monthly_bars_svg(points) -> str:
    """Mirror of the MonthlyBars React component, for the static preview."""
    vals = []
    for p in points:
        for k in ("net_actual", "budget", "prior_year_actual"):
            if p.get(k) is not None:
                vals.append(abs(float(p[k])))
    top = max(vals) if vals else 1.0
    n = len(points)
    w = 100 / n
    height, pad = 240, 34
    plot = height - pad

    def h(v):
        return (abs(float(v)) / top) * (plot - 12)

    bars = ""
    for i, p in enumerate(points):
        x = i * w
        if p.get("budget") is not None:
            bars += (f'<rect x="{x + w * 0.18:.3f}" y="{plot - h(p["budget"]):.2f}" '
                     f'width="{w * 0.64:.3f}" height="{h(p["budget"]):.2f}" '
                     f'class="bar-budget"/>')
        if p.get("net_actual") is not None:
            neg = " negative" if float(p["net_actual"]) < 0 else ""
            bars += (f'<rect x="{x + w * 0.3:.3f}" y="{plot - h(p["net_actual"]):.2f}" '
                     f'width="{w * 0.4:.3f}" height="{h(p["net_actual"]):.2f}" '
                     f'class="bar-actual{neg}"/>')
        if p.get("prior_year_actual") is not None:
            y = plot - h(p["prior_year_actual"])
            bars += (f'<line x1="{x + w * 0.12:.3f}" x2="{x + w * 0.88:.3f}" '
                     f'y1="{y:.2f}" y2="{y:.2f}" class="line-prior"/>')
    grid = "".join(
        f'<line x1="0" x2="100" y1="{plot - f * (plot - 12):.2f}" '
        f'y2="{plot - f * (plot - 12):.2f}" class="grid-line"/>'
        for f in (0.25, 0.5, 0.75, 1))
    axis = "".join(
        f'<span class="{"future" if not p["started"] else ""}">{html.escape(p["label"])}</span>'
        for p in points)
    return (f'<div class="chart"><svg viewBox="0 0 100 {height}" '
            f'preserveAspectRatio="none">{grid}{bars}</svg>'
            f'<div class="chart-axis">{axis}</div>'
            '<div class="chart-legend">'
            '<span><i class="swatch bar-actual"></i>Actual</span>'
            '<span><i class="swatch bar-budget"></i>Budget</span>'
            '<span><i class="swatch line-prior"></i>Prior year</span></div></div>')


def composition_svg(items) -> str:
    total = sum(abs(float(i["amount"])) for i in items) or 1
    segs, keys = "", ""
    for idx, i in enumerate(items):
        share = abs(float(i["amount"])) / total
        segs += (f'<div class="seg seg-{idx % 8}" style="width:{share * 100:.3f}%" '
                 f'title="{html.escape(i["classification"])}"></div>')
        keys += (f'<li><i class="swatch seg-{idx % 8}"></i>'
                 f'<span class="key-label">{html.escape(i["classification"])}</span>'
                 f'<span class="key-value">{money(i["amount"], raw=True)}</span>'
                 f'<span class="key-share">{share * 100:.1f}%</span></li>')
    return (f'<div class="composition"><div class="composition-bar">{segs}</div>'
            f'<ul class="composition-key">{keys}</ul></div>')


def change_bars_html(items, label_key, limit=10) -> str:
    shown = sorted(items, key=lambda r: -abs(float(r["change"])))[:limit]
    shown = sorted(shown, key=lambda r: -float(r["change"]))
    top = max((abs(float(r["change"])) for r in shown), default=1) or 1
    out = ""
    for r in shown:
        change = float(r["change"])
        pct = (abs(change) / top) * 50
        side = (f'left:50%;width:{pct:.2f}%' if change >= 0
                else f'right:50%;width:{pct:.2f}%')
        cls = "up" if change >= 0 else "down"
        sign = "+" if change >= 0 else "\u2212"
        out += (f'<li><span class="change-label">{html.escape(str(r[label_key]))}</span>'
                f'<span class="change-track"><span class="change-axis"></span>'
                f'<span class="change-fill {cls}" style="{side}"></span></span>'
                f'<span class="change-value {cls}">{sign}'
                f'{money(abs(change), raw=True)}</span></li>')
    return f'<ul class="change-bars">{out}</ul>'


def gauge_html(actual, budget, label) -> str:
    if actual is None or budget in (None, 0):
        return ('<div class="gauge na-gauge">No budget applies, so achievement '
                "is N/A.</div>")
    a, b = float(actual), float(budget)
    ratio = a / b
    pct = min(abs(ratio), 1.5) / 1.5 * 100
    over = ratio >= 1
    verdict = ("over budget" if over else "under budget")
    return (f'<div class="gauge {"over" if over else "under"}">'
            f'<div class="gauge-track"><span class="gauge-fill" '
            f'style="width:{pct:.2f}%"></span>'
            f'<span class="gauge-target" style="left:66.67%"></span></div>'
            f'<div class="gauge-verdict"><strong>{ratio * 100:.1f}%</strong> of {label} '
            f'&mdash; <span class="{"good" if over else "bad"}">{verdict} by '
            f'{money(abs(a - b), raw=True)} '
            f'({abs((ratio - 1) * 100):.1f}%)</span></div></div>')


# One source for the navigation, shared by the markup and the highlight CSS so
# the two cannot drift apart.
NAV_ITEMS = [
    ("business", "Business performance"),
    ("manager", "Account manager"),
    ("allmanagers", "All managers by month"),
    ("managers", "Compare managers"),
    ("movement", "Forecast history"),
    ("returns", "Return income"),
    ("policies", "Policy renewals"),
    ("review", "Matching review"),
    ("budget", "Budget"),
    ("bonus", "Bonus tracker"),
    ("dq", "Data quality"),
    ("uploads", "Uploads & audit"),
    ("settings", "Settings & mappings"),
]


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
    periods_data = client.get("/api/periods").json()
    maps = client.get("/api/reference/mappings").json()
    detail = client.get("/api/managers/Sam%20Stewart/detail?financial_year=2026").json()
    yoy = client.get("/api/analytics/year-over-year?financial_year=2026").json()
    matrix = client.get("/api/analytics/manager-matrix?financial_year=2026"
                        "&measure=net_actual").json()
    ret = client.get("/api/analytics/return-income").json()
    bonus = client.get("/api/bonus?financial_year=2026").json()
    history = client.get("/api/forecast-history?manager=Sam%20Stewart"
                         "&financial_year=2026").json()

    css = (ROOT / "web" / "src" / "styles.css").read_text()
    logo = base64.b64encode(
        (ROOT / "web" / "public" / "broker-plus-logo.png").read_bytes()).decode()

    # --- business ------------------------------------------------------------
    growth_up = float(yoy["ytd_growth"]["value"] or 0) >= 0
    verdict_cls = ("neutral" if yoy["on_track"] is None
                   else ("good" if yoy["on_track"] else "bad"))
    business_html = (
        f'<div class="verdict-bar {verdict_cls}"><strong>'
        f'{html.escape(yoy["verdict"])}</strong></div>')

    business_html += panel(
        "This year against last",
        f'Like for like: {yoy["prior_label"]} is cut at the same month of the year '
        "as the current reporting cut-off, so a part year is never compared with a "
        "full one.",
        '<div class="metric-grid">'
        + metric("Earned this year to date", money(yoy["ytd_actual"]), emphasis=True)
        + metric(f'Same period {yoy["prior_label"]}', money(yoy["ytd_prior_year"]))
        + metric("Growth on prior year",
                 ("+" if growth_up else "") + money(yoy["ytd_growth"]),
                 sub=("up " if growth_up else "down ")
                     + pct(yoy["ytd_growth_pct"]) + " on the same period",
                 emphasis=True)
        + metric(f'{yoy["prior_label"]} full year', money(yoy["prior_year_full"]),
                 hint="The whole prior year, for context. The budget is not derived "
                      "from it.")
        + "</div>")

    business_html += panel(
        "Against budget",
        "Budget is the Original Renewal Forecast plus the new business growth "
        "target. It does not move when the Latest Forecast moves.",
        gauge_html(yoy["ytd_actual"]["value"], yoy["ytd_budget"]["value"],
                   "year-to-date budget")
        + '<div class="metric-grid" style="margin-top:14px">'
        + metric("Year-to-date Budget", money(yoy["ytd_budget"]))
        + metric("Variance to Budget", money(yoy["ytd_variance"]))
        + metric("Full-year Budget", money(yoy["full_year_budget"]))
        + metric("Latest Outlook", money(yoy["latest_outlook"]))
        + metric("Remaining Budget Gap", money(yoy["remaining_gap"]))
        + metric("Outlook vs prior year", pct(yoy["outlook_vs_prior_year_pct"]))
        + "</div>")

    business_html += panel(
        "Month by month",
        "Bars are actual against budget; the line is the same month last year. "
        "Months that have not started are left empty rather than drawn as zero.",
        monthly_bars_svg(yoy["months"]))

    business_html += (
        '<div class="two-col">'
        + panel("Where the growth is coming from",
                "Change on the same period last year, by account manager.",
                change_bars_html(yoy["growth_by_manager"], "canonical_manager"))
        + panel("By transaction type", "Which kinds of business moved.",
                change_bars_html(yoy["growth_by_type"], "classification"))
        + "</div>")

    business_html += panel(
        "Income and leakage", "",
        '<div class="metric-grid">'
        + metric("Positive Actual Income", money(biz["positive_actual_income"]))
        + metric("Return Income", money(biz["return_income"]),
                 hint="Money that came back out. Reduces Net Actual Income.")
        + metric("Net Actual Income", money(biz["net_actual_income"]), emphasis=True)
        + metric("Actual New Business", money(biz["actual_new_business"]))
        + metric("Lapse / Lost Renewal", money(biz["lapse_return_income"]))
        + metric("Mid-Term Cancellation",
                 money(biz["midterm_cancellation_return_income"]))
        + metric("New Business Cancellation",
                 money(biz["new_business_cancellation_return_income"]))
        + metric("Negative Endorsements", money(biz["negative_endorsements"]))
        + "</div>")

    business_html += panel(
        "Renewal forecast", "",
        '<div class="metric-grid">'
        + metric("Original Renewal Forecast", money(biz["original_renewal_forecast"]))
        + metric("Latest Renewal Forecast", money(biz["latest_renewal_forecast"]),
                 hint="A completed month has no Latest Forecast; it reports actuals.")
        + metric("Forecast Movement", money(biz["forecast_movement"]))
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

    # --- forecast history ----------------------------------------------------
    hhead = ('<th class="sticky-col">Forecast recorded</th>'
             + "".join(f'<th class="right">{dt.date.fromisoformat(m):%b %Y}</th>'
                       for m in history["months"])
             + '<th class="right total-col">Total</th>')
    hbody = ""
    for e in history["entries"]:
        chips = ('<span class="chip current">current</span>' if e["is_current"] else "")
        if e["kind"] != "snapshot":
            chips += '<span class="chip">baseline</span>'
        stamp = (e["recorded_at"] or "")[:16].replace("T", " ")
        meta_line = f'{stamp} &middot; {html.escape(e["recorded_by"] or "")}'
        if e["source_file"]:
            meta_line += f' &middot; <code>{html.escape(e["source_file"])}</code>'
        cells = ""
        for c in e["cells"]:
            if c["value"] is None:
                cells += ('<td class="right"><span class="not-yet" '
                          'title="This forecast did not cover this month.">'
                          "&mdash;</span></td>")
            else:
                delta = ""
                if c["change"] is not None:
                    up = float(c["change"]) >= 0
                    delta = (f'<span class="delta {"up" if up else "down"}">'
                             f'{"&#9650;" if up else "&#9660;"} '
                             f'{money(abs(float(c["change"])), raw=True)}</span>')
                cells += f'<td class="right">{money(c["value"], raw=True)}{delta}</td>'
        total = money(e["total"], raw=True) if e["total"] is not None else "&mdash;"
        hbody += (f'<tr class="{"history-current" if e["is_current"] else ""}">'
                  f'<td class="sticky-col"><div class="history-label">'
                  f'<strong>{html.escape(e["label"])}</strong>{chips}</div>'
                  f'<div class="history-meta">{meta_line}</div></td>'
                  f'{cells}<td class="right total-col">{total}</td></tr>')

    movement_html = (
        '<div class="purpose"><strong>What this page is for.</strong> A record of '
        "what was forecast for each month, and when. Every accepted Renewals "
        "Pending file adds a row, time stamped and attributed. Read down a column "
        "to see how the expectation for that month changed; read across a row to "
        "see one forecast as it stood on the day it arrived.</div>")
    movement_html += panel(
        f'{history["entry_count"]} forecasts recorded for '
        f'{history["canonical_manager"]}',
        "Oldest first. The most recent Renewals Pending file is marked current.",
        f'<div class="table-wrap"><table class="grid"><thead><tr>{hhead}</tr></thead>'
        f'<tbody>{hbody}</tbody></table></div>')

    # --- returns -------------------------------------------------------------
    returns_html = panel(
        "How much came back out",
        "Return income is money that left again after being earned. It is shown as "
        "a positive amount and reduces Net Actual Income.",
        '<div class="metric-grid">'
        + metric("Positive Actual Income", money(ret["positive_income"], raw=True))
        + metric("Return Income", money(ret["total_return_income"], raw=True),
                 emphasis=True)
        + metric("Net Actual Income", money(ret["net_income"], raw=True), emphasis=True)
        + metric("Return rate",
                 f'<span class="val">{Decimal(str(ret["return_rate"])) * 100:.1f}%</span>',
                 hint="Return income as a share of positive income. The proportion of "
                      "what you earned that came back out.")
        + "</div>")
    returns_html += panel("What it was made of",
                          "In the running app each segment is clickable.",
                          composition_svg(ret["items"]))
    returns_html += panel(
        "By category", "",
        table([
            {"label": "Classification",
             "render": lambda r: html.escape(r["classification"])},
            {"label": "Return income", "right": 1,
             "render": lambda r: money(r["amount"], raw=True)},
            {"label": "Share of returns", "right": 1,
             "render": lambda r: f'<span class="val">'
                                 f'{Decimal(str(r["share_of_returns"])) * 100:.1f}%</span>'},
            {"label": "Of positive income", "right": 1,
             "render": lambda r: f'<span class="val">'
                                 f'{Decimal(str(r["share_of_positive_income"])) * 100:.2f}%</span>'},
            {"label": "Transactions", "right": 1,
             "render": lambda r: f'<span class="val">{r["transactions"]}</span>'},
            {"label": "Average each", "right": 1,
             "render": lambda r: money(r["average_per_transaction"], raw=True)},
        ], ret["items"], "return income"))

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

    # --- manager detail ------------------------------------------------------
    def gcell(c, kind):
        if c["status"] == "future":
            return '<span class="not-yet" title="This month has not started yet.">&mdash;</span>'
        if c["status"] == "unavailable" or c["value"] is None:
            reason = html.escape(c.get("reason") or "Not available")
            return f'<span class="na" title="{reason}">N/A<span class="na-mark">?</span></span>'
        if kind == "verdict":
            return "YES" if float(c["value"]) >= 1 else "NO"
        if kind == "percent":
            pctv = Decimal(str(c["value"])) * 100
            sign = "+" if pctv > 0 else ""
            return f'<span class="val">{sign}{pctv:.1f}%</span>'
        if kind == "count":
            return f'<span class="val">{int(float(c["value"]))}</span>'
        return money(c["value"], raw=True)

    def row_kind(row):
        return row.get("value_kind", "money")

    def cell_tone(row, c):
        if c["status"] != "actual" or c["value"] is None:
            return ""
        if row.get("value_kind") == "verdict":
            return " cell-yes" if float(c["value"]) >= 1 else " cell-no"
        if row["label"].startswith("% Above") or row["label"].startswith("$ Above"):
            return " cell-good" if float(c["value"]) >= 0 else " cell-bad"
        return " negative" if float(c["value"]) < 0 else ""

    head = ('<th class="sticky-col">Transaction type / measure</th>'
            + "".join(f'<th class="right{" future-col" if st == "future" else ""}">'
                      f'{dt.date.fromisoformat(m):%b %Y}</th>'
                      for m, st in zip(detail["months"], detail["month_status"]))
            + '<th class="right total-col">Total</th>')
    body = ""
    for row in detail["rows"]:
        kind = row_kind(row)
        hint = (f'<span class="hint" title="{html.escape(row["hint"])}">i</span>'
                if row.get("hint") else "")
        cells = "".join(
            f'<td class="right{" future-col" if st == "future" else ""}'
            f'{cell_tone(row, c)}">{gcell(c, kind)}</td>'
            for c, st in zip(row["cells"], detail["month_status"]))
        if row["total"] is None:
            total = "&mdash;" if kind not in ("percent", "verdict") else ""
        elif kind == "count":
            total = f'<span class="val">{int(float(row["total"]))}</span>'
        elif kind == "percent":
            total = f'<span class="val">{Decimal(str(row["total"])) * 100:.1f}%</span>'
        else:
            total = money(row["total"], raw=True)
        body += (f'<tr class="grid-row grid-{row["kind"]}">'
                 f'<td class="sticky-col">{html.escape(row["label"])}{hint}</td>'
                 f'{cells}<td class="right total-col">{total}</td></tr>')

    detail_html = (
        '<div class="purpose"><strong>Static preview limitation.</strong> This page '
        'renders one manager. In the running application there is a dropdown here '
        'listing every account manager, plus a financial-year selector and a '
        'monthly/quarterly toggle.</div>')
    detail_html += panel(
        "Where this manager stands",
        f'Year to date is measured to the reporting cut-off, {detail["cut_off_month"]}. '
        "Months after that have not started.",
        '<div class="metric-grid">'
        + metric("Year-to-date Actual", money(detail["ytd_actual"]), emphasis=True)
        + metric("Year-to-date Budget", money(detail["ytd_budget"]),
                 sub=f'Achievement {pct(detail["ytd_achievement"])}')
        + metric("Full-year Budget", money(detail["full_year_budget"]))
        + metric("Latest Outlook", money(detail["latest_outlook"]))
        + metric("Remaining Budget Gap", money(detail["remaining_budget_gap"]))
        + metric("Prior Year Actual", money(detail["prior_year_actual"]),
                 hint="For comparison only. The budget is not derived from it.")
        + "</div>")
    growth_txt = (f'{Decimal(str(detail["active_growth_pct"]["value"])) * 100:.2f}%'
                  if detail["active_growth_pct"]["available"] else "N/A")
    detail_html += panel(
        "Budget growth percentage",
        "Set per manager. The budget follows from it directly.",
        f'<div class="growth-current"><span>Growth % currently applied to '
        f'{html.escape(detail["canonical_manager"])}:</span><strong>{growth_txt}</strong>'
        f'<span class="chip">from {detail["active_growth_basis"] or "default"}</span>'
        '<span class="growth-formula">Budget = Renewal Forecast + '
        '(Renewal Forecast &times; this %)</span></div>')
    detail_html += panel(
        "Month by month",
        "An em dash means the month has not started. N/A means the measure is "
        "unavailable and the tooltip says why.",
        f'<div class="table-wrap"><table class="grid"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>")

    # --- all managers matrix -------------------------------------------------
    mhead = ('<th class="sticky-col">Account manager</th>'
             + "".join(f'<th class="right{" future-col" if st == "future" else ""}">'
                       f'{dt.date.fromisoformat(m):%b %Y}</th>'
                       for m, st in zip(matrix["months"], matrix["month_status"]))
             + '<th class="right total-col">Total</th>')
    mbody = ""
    for r in matrix["rows"]:
        cells = ""
        for c, st in zip(r["cells"], matrix["month_status"]):
            if c["status"] == "future":
                v = ('<span class="not-yet" title="This month has not started yet.">'
                     "&mdash;</span>")
            elif c["value"] is None:
                v = '<span class="na">N/A</span>'
            else:
                v = money(c["value"], raw=True)
            mbody += ""
            cells += (f'<td class="right{" future-col" if st == "future" else ""}">'
                      f"{v}</td>")
        total = money(r["total"], raw=True) if r["total"] is not None else "&mdash;"
        mbody += (f'<tr><td class="sticky-col">{html.escape(r["canonical_manager"])}</td>'
                  f'{cells}<td class="right total-col">{total}</td></tr>')
    matrix_html = panel(
        "Net Actual by manager and month",
        "Every manager down the side, every month across the top. One measure at a "
        "time, so no figure can be mistaken for another. The running app switches "
        "between Net Actual, Budget, Variance, Achievement and Original Forecast.",
        f'<div class="table-wrap"><table class="grid"><thead><tr>{mhead}</tr></thead>'
        f'<tbody>{mbody}</tbody></table></div>')

    # --- settings ------------------------------------------------------------
    settings_html = (
        '<div class="purpose"><strong>What this page is for.</strong> The things '
        'that need maintaining as the business changes: the reporting cut-off, how '
        'source manager names map to reporting managers, and how policy classes '
        'from the two systems line up. Every change is recorded with the user and '
        'reason, and applies to past periods as well as future ones.</div>')
    settings_html += panel(
        "Reporting cut-off date",
        "The line between completed and future periods. Moving it backwards past "
        "months that already hold transactions is refused.",
        '<div class="metric-grid">'
        + metric("Current cut-off",
                 f'<span class="val">{periods_data["cut_off_date"]}</span>')
        + metric("Current financial year",
                 f'<span class="val">{periods_data["current_financial_year_label"]}'
                 "</span>",
                 sub=f'Q{periods_data["current_quarter"]}')
        + metric("Financial years in data",
                 f'<span class="val">{len(periods_data["financial_years"])}</span>',
                 sub=", ".join(y["label"] for y in periods_data["financial_years"]))
        + "</div>")
    settings_html += panel(
        f'Policy classes needing a mapping ({len(maps["unmapped_classes"])})',
        "An unmapped class still matches on client and policy number, but cannot "
        "reach the top matching tier. Mapping them is an administrator task, not a "
        "code change.",
        table([
            {"label": "Source", "render": lambda r: esc(r["source_type"])},
            {"label": "Class value", "render": lambda r: esc(r["source_value"])},
            {"label": "Records", "right": 1,
             "render": lambda r: f'<span class="val">{r["records"]}</span>'},
        ], maps["unmapped_classes"][:15], "unmapped classes"))
    settings_html += panel(
        "Manager aliases",
        "Applied by join at read time, so a correction fixes actuals, forecasts "
        "and budgets together rather than only new records.",
        table([
            {"label": "Source name", "render": lambda r: esc(r["source_manager"])},
            {"label": "Reports as", "render": lambda r: esc(r["canonical_manager"])},
            {"label": "Status", "render": lambda r: esc(r["status"])},
            {"label": "In rankings",
             "render": lambda r: "Yes" if r["include_in_rankings"] else "No"},
        ], maps["manager_aliases"], "aliases"))

    # --- bonus tracker -------------------------------------------------------
    bonus_html = (
        '<div class="purpose"><strong>How the bonus works.</strong> '
        + html.escape(bonus["scheme"]["description"])
        + '<ul class="formula">'
        + "".join(f'<li><code>{html.escape(f)}</code></li>'
                  for f in bonus["scheme"]["formula"])
        + "</ul><em>Earned is not projected.</em> A quarter still running shows "
          "the bonus that would pay if it closed today &mdash; usually nil "
          "part-way through &mdash; with the projection at current pace reported "
          "separately. Projections are not money.</div>")
    bonus_html += panel(
        "Position across the business", "",
        '<div class="metric-grid">'
        + metric("Bonus earned so far", money(bonus["totals"]["earned_bonus"], raw=True),
                 emphasis=True,
                 hint="Payable on the figures to date. A quarter still open normally "
                      "shows nil.")
        + metric("Projected at current pace",
                 money(bonus["totals"]["projected_bonus"], raw=True),
                 hint="Not money earned.")
        + metric("Bonus if every target is exactly met",
                 money(bonus["totals"]["bonus_at_target"], raw=True))
        + metric("Total actual income", money(bonus["totals"]["actual_income"], raw=True))
        + metric("Total budget target", money(bonus["totals"]["budget_target"], raw=True))
        + "</div>")
    bonus_html += panel(
        "Bonus by manager, year to date", "",
        table([
            {"label": "Manager", "render": lambda r: esc(r["canonical_manager"])},
            {"label": "Actual (started quarters)", "right": 1,
             "render": lambda r: money(r["ytd_actual"], raw=True)},
            {"label": "Target", "right": 1,
             "render": lambda r: money(r["ytd_budget_target"], raw=True)},
            {"label": "Above / (below)", "right": 1,
             "render": lambda r: money(float(r["ytd_actual"])
                                       - float(r["ytd_budget_target"]), raw=True)},
            {"label": "Bonus earned", "right": 1,
             "render": lambda r: money(r["earned_bonus"], raw=True)},
            {"label": "Projected", "right": 1,
             "render": lambda r: money(r["projected_bonus"], raw=True)},
            {"label": "If targets met", "right": 1,
             "render": lambda r: money(r["bonus_at_target"], raw=True)},
        ], bonus["managers"], "managers"))

    STATUS = {"earned": "Earned", "missed": "Missed", "on track": "On track",
              "behind": "Behind", "not started": "Not started"}
    bonus_html += panel(
        "Every quarter",
        "A quarter that has not started carries no bonus figure at all, rather "
        "than a nil.",
        table([
            {"label": "Manager", "render": lambda r: esc(r["canonical_manager"])},
            {"label": "Quarter", "render": lambda r: esc(r["quarter_label"])},
            {"label": "Months",
             "render": lambda r: f'{r["months_elapsed"]}/{r["months_in_quarter"]}'},
            {"label": "Expected income", "right": 1,
             "render": lambda r: money(r["expected_income"], raw=True)},
            {"label": "Growth %", "right": 1,
             "render": lambda r: (f'<span class="val">'
                                  f'{Decimal(str(r["growth_pct"])) * 100:.1f}%</span>'
                                  if r["growth_pct"] is not None else NA_HTML)},
            {"label": "Budget target", "right": 1,
             "render": lambda r: money(r["budget_target"], raw=True)},
            {"label": "Actual", "right": 1,
             "render": lambda r: (money(r["actual_income"], raw=True)
                                  if r["quarter_started"] else NA_HTML)},
            {"label": "Above / (below)", "right": 1,
             "render": lambda r: (money(r["above_below_target"], raw=True)
                                  if r["quarter_started"] else NA_HTML)},
            {"label": "Still needed", "right": 1,
             "render": lambda r: (money(r["income_still_required"], raw=True)
                                  if r["quarter_started"] else NA_HTML)},
            {"label": "Bonus earned", "right": 1,
             "render": lambda r: (money(r["total_bonus"], raw=True)
                                  if r["total_bonus"] is not None else NA_HTML)},
            {"label": "Projected", "right": 1,
             "render": lambda r: (money(r["projected_bonus"], raw=True)
                                  if r["projected_bonus"] is not None else NA_HTML)},
            {"label": "Status", "render": lambda r:
             f'<span class="verdict-chip status-{r["status"].replace(" ", "-")}">'
             f'{STATUS.get(r["status"], r["status"])}</span>'},
        ], bonus["quarters"], "quarters"))

    nav = "".join(f'<a href="#{k}" id="nav-{k}">{v}</a>' for k, v in NAV_ITEMS)

    # The nav previously hardcoded the first item as active, so "Business
    # performance" stayed highlighted whichever section was open. :has() lets a
    # static page mark the link matching the section in view.
    nav_active_css = "\n".join(
        f'body:has(#{k}:target) #nav-{k} {{ background:#17242f; color:#fff; '
        f'border-left-color: var(--accent); }}'
        for k, _ in NAV_ITEMS)
    nav_active_css += ("\nbody:not(:has(.page:target)) #nav-business "
                       "{ background:#17242f; color:#fff; "
                       "border-left-color: var(--accent); }")

    banner = (f'<div class="gst-banner"><strong>All income figures are GST inclusive.'
              f'</strong><span class="gst-meta">Reporting cut-off '
              f'{biz["meta"]["cut_off_date"]} &middot; Australia/Melbourne</span></div>')
    notes = ("<ul class='notes'>"
             + "".join(f"<li>{html.escape(n)}</li>" for n in biz["meta"]["notes"])
             + "</ul>")
    warning = (
        '<div class="warning"><strong>July 2026 uses supplied forecast figures.'
        '</strong> A renewal forecast per manager was entered directly, held at '
        'manager-month level, because the Renewals Pending file was extracted '
        'after most of that month\'s renewals had already transacted. Actuals come '
        'from Sales Transactions. Policy-level renewal detail begins August 2026.'
        '</div>')
    base_note = ("" if base["is_base_state"] else
                 '<div class="warning small">The database is not in the clean base '
                 "state.</div>")

    sections = [
        ("business", "Overall business performance", "FY2026-27",
         banner + notes + warning + business_html),
        ("manager", f'{detail["canonical_manager"]}', detail["financial_year_label"],
         banner + detail_html),
        ("allmanagers", "All managers by month", "FY2026-27", banner + matrix_html),
        ("managers", "Compare managers", "FY2026-27", banner + managers_html),
        ("movement", "Forecast history",
         f'{history["canonical_manager"]} · {history["financial_year_label"]}',
         banner + movement_html),
        ("returns", "Return income", "", banner + returns_html),
        ("policies", "Policy-level renewals", "", banner + policies_html),
        ("review", "Matching review queue", "", banner + review_html),
        ("budget", "Budget", "FY2026-27", banner + budget_html),
        ("bonus", "Bonus tracker", bonus["financial_year_label"], banner + bonus_html),
        ("dq", "Data quality", "", banner + dq_html),
        ("uploads", "Uploads and audit history", "", banner + uploads_html),
        ("settings", "Settings and mappings", "", banner + settings_html),
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
{nav_active_css}
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
    <div class="brand"><img class="brand-logo" src="data:image/png;base64,{logo}"
                            alt="Broker+" width="38" height="38">
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
