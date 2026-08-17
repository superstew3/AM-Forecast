#!/usr/bin/env python3
"""Build the demo walkthrough PDF.

The figures are not mockups. Each one is the real markup of the running
interface, with the real stylesheet and the real figures from the database,
lifted out of the generated preview and re-rendered for print. If a screen
changes, regenerating the preview and rerunning this produces a document that
matches it.

    python build_demo.py <preview.html> <facts.json> <out.pdf>
"""
from __future__ import annotations

import html as H
import json
import re
import sys
from pathlib import Path

# --------------------------------------------------------------------------
# Extracting real screens
# --------------------------------------------------------------------------


class Preview:
    """The generated preview, indexed by page and panel."""

    def __init__(self, path: Path):
        raw = path.read_text()
        self.css = re.search(r"<style>(.*?)</style>", raw, re.S).group(1)
        self.logo = re.search(r'src="data:image/png;base64,([^"]+)"', raw).group(1)
        starts = [(m.group(1), m.start())
                  for m in re.finditer(r'<section id="([^"]+)" class="page">', raw)]
        starts.append(("__end__", raw.find("</main>")))
        self.pages: dict[str, str] = {}
        for (pid, a), (_, b) in zip(starts, starts[1:]):
            self.pages[pid] = raw[a:b]

    def panels(self, page: str) -> list[str]:
        """Every <section class="panel"> on a page, in order."""
        body = self.pages[page]
        out, depth, start = [], 0, None
        for m in re.finditer(r'<section class="panel">|<section|</section>', body):
            tag = m.group(0)
            if tag == '<section class="panel">':
                if depth == 0:
                    start = m.start()
                depth += 1
            elif tag == "<section":
                if depth:
                    depth += 1
            else:
                if depth:
                    depth -= 1
                    if depth == 0:
                        out.append(body[start:m.end()])
        return out

    def panel(self, page: str, index: int) -> str:
        return self.panels(page)[index]

    def banner(self, page: str) -> str:
        m = re.search(r'(<div class="gst-banner">.*?</div>\s*</div>)',
                      self.pages[page], re.S)
        return m.group(1) if m else ""

    def heading(self, page: str) -> str:
        m = re.search(r"<h1>(.*?)</h1>", self.pages[page], re.S)
        return re.sub(r"<[^>]+>", " ", m.group(1)).strip() if m else page

    def purpose(self, page: str) -> str:
        m = re.search(r'(<div class="purpose">.*?</div>)', self.pages[page], re.S)
        return m.group(1) if m else ""


# --------------------------------------------------------------------------
# Document pieces
# --------------------------------------------------------------------------

def trim_rows(fragment: str, keep: int, marker: str = "tr") -> str:
    """Keep the first `keep` repeated elements of a fragment.

    A figure showing fourteen managers or forty table rows runs over a page
    break and the crop stops being reliable. Showing a representative extract
    and saying so in the caption is clearer than a figure sliced mid-row.
    """
    if marker == "tr":
        rows = re.findall(r"<tr>.*?</tr>", fragment, re.S)
        body = re.search(r"<tbody>(.*?)</tbody>", fragment, re.S)
        if not body:
            return fragment
        inner = re.findall(r"<tr>.*?</tr>", body.group(1), re.S)
        if len(inner) <= keep:
            return fragment
        return fragment.replace(body.group(1), "".join(inner[:keep]))

    parts = re.split(rf'(?=<div class="{marker}")', fragment)
    head, cards = parts[0], parts[1:]
    if len(cards) <= keep:
        return fragment
    tail = cards[-1]
    closing = tail[tail.rindex("</div>"):] if "</div>" in tail else ""
    return head + "".join(cards[:keep]) + closing


def select_rows(fragment: str, labels: list[str]) -> str:
    """Keep only the named rows of a grid, in their original order.

    Showing the first N rows of the manager grid cuts it off before the budget
    and verdict rows, which are the part worth showing. Selecting by label keeps
    the chain intact: income, forecast, growth, budget, verdict.
    """
    body = re.search(r"<tbody>(.*?)</tbody>", fragment, re.S)
    if not body:
        return fragment
    rows = re.findall(r"<tr.*?</tr>", body.group(1), re.S)
    kept = [r for r in rows
            if any(f">{lbl}" in re.sub(r"<span[^>]*>.*?</span>", "", r, flags=re.S)
                   for lbl in labels)]
    return fragment.replace(body.group(1), "".join(kept)) if kept else fragment


def trim_cols(fragment: str, keep: int) -> str:
    """Keep the first `keep` data columns plus the last (the total)."""
    def cut(row: str) -> str:
        cells = re.findall(r"<t[hd].*?</t[hd]>", row, re.S)
        if len(cells) <= keep + 2:
            return row
        selected = cells[:keep + 1] + [cells[-1]]
        prefix = row[:row.index(cells[0])]
        suffix = row[row.rindex(cells[-1]) + len(cells[-1]):]
        return prefix + "".join(selected) + suffix

    return re.sub(r"<tr.*?</tr>", lambda m: cut(m.group(0)), fragment, flags=re.S)


def esc(text: str) -> str:
    return H.escape(text)


def money(value) -> str:
    v = float(value)
    return f"(${abs(v):,.2f})" if v < 0 else f"${v:,.2f}"


class Doc:
    def __init__(self, preview: Preview, facts: dict):
        self.pv = preview
        self.f = facts
        self.parts: list[str] = []
        self.contents: list[tuple[str, str]] = []
        self._fig = 0

    # -- structure ---------------------------------------------------------

    def page_break(self) -> None:
        self.parts.append('<div class="page-break"></div>')

    def section(self, number: str, title: str, standfirst: str = "") -> None:
        anchor = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        self.contents.append((f"{number}  {title}", anchor))
        self.parts.append(
            f'<h2 id="{anchor}"><span class="secnum">{number}</span>{esc(title)}</h2>')
        if standfirst:
            self.parts.append(f'<p class="standfirst">{standfirst}</p>')

    def h3(self, title: str) -> None:
        self.parts.append(f"<h3>{esc(title)}</h3>")

    def p(self, text: str) -> None:
        self.parts.append(f"<p>{text}</p>")

    def purpose_box(self, text: str) -> None:
        self.parts.append(
            f'<div class="purpose-box"><span class="purpose-label">Purpose</span>'
            f"<p>{text}</p></div>")

    def bullets(self, items: list[str], tight: bool = False) -> None:
        cls = ' class="tight"' if tight else ""
        self.parts.append(f"<ul{cls}>" + "".join(f"<li>{i}</li>" for i in items)
                          + "</ul>")

    def steps(self, items: list[str]) -> None:
        self.parts.append('<ol class="steps">'
                          + "".join(f"<li>{i}</li>" for i in items) + "</ol>")

    def table(self, headers: list[str], rows: list[list[str]],
              right: set[int] | None = None, cls: str = "") -> None:
        right = right or set()
        head = "".join(
            f'<th{" class=\'right\'" if i in right else ""}>{esc(h)}</th>'
            for i, h in enumerate(headers))
        body = ""
        for r in rows:
            body += "<tr>" + "".join(
                f'<td{" class=\'right\'" if i in right else ""}>{c}</td>'
                for i, c in enumerate(r)) + "</tr>"
        self.parts.append(f'<table class="doc-table {cls}"><thead><tr>{head}</tr>'
                          f"</thead><tbody>{body}</tbody></table>")

    def callout(self, title: str, text: str, tone: str = "note") -> None:
        self.parts.append(
            f'<div class="callout {tone}"><strong>{esc(title)}</strong> {text}</div>')

    # -- figures -----------------------------------------------------------

    def figure(self, body_html: str, caption: str, notes: list[str],
               scale: str = "", crop: bool = False) -> None:
        """A real screen fragment, with numbered notes beneath it.

        Numbers are carried in the note list rather than floated over the image.
        Absolutely positioned badges look precise but drift the moment a figure
        reflows at a different width, and a badge pointing at the wrong cell is
        worse than no badge at all.
        """
        self._fig += 1
        legend = "".join(
            f'<li><span class="badge">{i + 1}</span><span>{n}</span></li>'
            for i, n in enumerate(notes))
        self.parts.append(
            f'<figure class="screen">'
            f'<div class="screen-chrome"><span class="dot"></span>'
            f'<span class="dot"></span><span class="dot"></span>'
            f'<span class="screen-url">Income Forecasting &mdash; Broker+</span></div>'
            f'<div class="screen-body {scale}{" cropped" if crop else ""}">'
            f"{body_html}</div>"
            f'<figcaption><span class="figno">Figure {self._fig}</span>'
            f"{caption}</figcaption>"
            f'<ol class="fignotes">{legend}</ol>'
            "</figure>")

    # -- output ------------------------------------------------------------

    def render(self) -> str:
        toc = "".join(
            f'<li><a href="#{a}">{esc(t)}</a></li>' for t, a in self.contents)
        return TEMPLATE.format(css=self.pv.css, doc_css=DOC_CSS, logo=self.pv.logo,
                               toc=toc, body="".join(self.parts))


TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Income Forecasting — walkthrough</title>
<style>{css}</style>
<style>{doc_css}</style>
</head><body>

<section class="cover">
  <img class="cover-logo" src="data:image/png;base64,{logo}" alt="Broker+">
  <div class="cover-kicker">Broker+</div>
  <h1 class="cover-title">Income Forecasting</h1>
  <div class="cover-sub">Performance, budget and bonus for the account management book</div>
  <div class="cover-rule"></div>
  <div class="cover-meta">
    <div><span>Document</span>Demonstration walkthrough and user guide</div>
    <div><span>Covers</span>Every page and feature, with purpose, use and worked figures</div>
    <div><span>Data shown</span>Live figures, FY2026-27, reporting cut-off 31 July 2026</div>
    <div><span>Replaces</span>Account Manager Sales Dashboard FY2025-27 (Excel)</div>
  </div>
  <div class="cover-note">
    Every screen in this document is the running application rendered with real
    data. Nothing here is a mock-up.
  </div>
</section>

<div class="page-break"></div>

<section class="toc">
  <h2 class="toc-h">Contents</h2>
  <ol class="toc-list">{toc}</ol>
</section>

<div class="page-break"></div>

<main class="doc">{body}</main>
</body></html>"""


DOC_CSS = """
@page {
  size: A4; margin: 18mm 16mm 20mm 16mm;
  @bottom-center {
    content: counter(page); font-family: -apple-system, "Segoe UI", sans-serif;
    font-size: 8.5pt; color: #8b98a5;
  }
  @bottom-right {
    content: "Income Forecasting — walkthrough";
    font-family: -apple-system, "Segoe UI", sans-serif;
    font-size: 7.5pt; color: #b3bec8;
  }
}
@page :first { @bottom-center { content: ""; } @bottom-right { content: ""; } }

body { background: #fff; font-size: 10pt; line-height: 1.55; color: #1a2732; }
.page-break { break-after: page; }

/* cover */
.cover { padding-top: 26mm; }
.cover-logo { width: 74px; height: 74px; object-fit: contain; }
.cover-kicker { margin-top: 18px; font-size: 9pt; letter-spacing: .22em;
  text-transform: uppercase; color: #1f4b6e; font-weight: 700; }
.cover-title { font-size: 34pt; line-height: 1.05; margin: 6px 0 0; font-weight: 700;
  letter-spacing: -0.5px; color: #101b25; }
.cover-sub { font-size: 13pt; color: #55636f; margin-top: 8px; }
.cover-rule { height: 3px; width: 76px; background: #d4622a; margin: 26px 0 22px; }
.cover-meta div { display: flex; gap: 14px; font-size: 9.5pt; padding: 5px 0;
  border-bottom: 1px solid #e7ecf0; }
.cover-meta span { width: 96px; color: #8b98a5; flex: none; text-transform: uppercase;
  font-size: 7.5pt; letter-spacing: .08em; padding-top: 2px; }
.cover-note { margin-top: 30px; font-size: 9pt; color: #55636f; max-width: 78%;
  border-left: 3px solid #cfe0ec; padding-left: 12px; }

/* contents */
.toc-h { font-size: 17pt; margin: 0 0 14px; }
.toc-list { list-style: none; padding: 0; margin: 0; column-count: 1; }
.toc-list li { padding: 5px 0; border-bottom: 1px dotted #dfe6ec; font-size: 10pt; }
.toc-list a { color: #1a2732; text-decoration: none; }
.toc-list a::after { content: leader('.') target-counter(attr(href), page);
  color: #8b98a5; }

/* headings */
.doc h2 { font-size: 16pt; margin: 0 0 4px; padding-top: 2px; break-after: avoid;
  color: #101b25; letter-spacing: -0.2px; }
.doc h2 .secnum { display: inline-block; min-width: 30px; color: #d4622a; }
.doc h3 { font-size: 11.5pt; margin: 18px 0 5px; break-after: avoid; color: #1f4b6e; }
.standfirst { font-size: 11pt; color: #55636f; margin: 0 0 14px; line-height: 1.5; }
.doc p { margin: 0 0 9px; }
.doc ul, .doc ol { margin: 0 0 10px; padding-left: 20px; }
.doc li { margin-bottom: 4px; }
.doc ul.tight li { margin-bottom: 1px; }
ol.steps { counter-reset: s; list-style: none; padding-left: 0; }
ol.steps li { counter-increment: s; position: relative; padding-left: 26px;
  margin-bottom: 6px; }
ol.steps li::before { content: counter(s); position: absolute; left: 0; top: 1px;
  width: 17px; height: 17px; border-radius: 50%; background: #1f4b6e; color: #fff;
  font-size: 8pt; font-weight: 700; text-align: center; line-height: 17px; }
code { background: #f1f4f7; padding: 1px 4px; border-radius: 3px; font-size: 8.6pt; }

/* purpose box */
.purpose-box { border-left: 3px solid #d4622a; background: #fdf6f2; padding: 10px 14px;
  margin: 0 0 14px; break-inside: avoid; }
.purpose-label { display: block; font-size: 7.5pt; letter-spacing: .14em;
  text-transform: uppercase; color: #d4622a; font-weight: 700; margin-bottom: 3px; }
.purpose-box p { margin: 0; font-size: 10pt; }

/* callouts */
.callout { border: 1px solid #dfe6ec; background: #f8fafb; border-radius: 5px;
  padding: 9px 13px; margin: 10px 0 12px; font-size: 9.3pt; break-inside: avoid; }
.callout strong { color: #101b25; }
.callout.watch { background: #fdf8ee; border-color: #eadfc2; }
.callout.good { background: #f2f9f4; border-color: #cfe6d8; }

/* document tables */
table.doc-table { width: 100%; border-collapse: collapse; margin: 6px 0 14px;
  font-size: 9pt; break-inside: avoid; }
table.doc-table th { background: #eef2f5; text-align: left; padding: 6px 9px;
  font-size: 7.8pt; text-transform: uppercase; letter-spacing: .05em; color: #55636f;
  border-bottom: 1px solid #d8e0e7; }
table.doc-table td { padding: 6px 9px; border-bottom: 1px solid #edf1f4;
  vertical-align: top; }
table.doc-table .right { text-align: right; font-variant-numeric: tabular-nums; }
table.doc-table.compact td, table.doc-table.compact th { padding: 4px 8px; }

/* figures: real screens */
figure.screen { margin: 12px 0 16px; break-inside: avoid;
  border: 1px solid #d5dee6; border-radius: 7px; overflow: hidden;
  box-shadow: 0 1px 3px rgba(16,27,37,.07); }
.screen-chrome { background: #eef2f5; border-bottom: 1px solid #d5dee6;
  padding: 5px 10px; display: flex; align-items: center; gap: 5px; }
.screen-chrome .dot { width: 7px; height: 7px; border-radius: 50%; background: #c6d0d9;
  display: inline-block; }
.screen-url { margin-left: 8px; font-size: 7.5pt; color: #7e8b98; }
.screen-body { padding: 10px 12px; background: #f6f8f9; font-size: 8.6pt; }
.screen-body.s90 { font-size: 8pt; }
.screen-body.s80 { font-size: 7.2pt; }
.screen-body.s70 { font-size: 6.4pt; }
.screen-body.cropped { max-height: 105mm; overflow: hidden; }
figure.screen.tall { break-inside: auto; }
figure.screen.tall figcaption, figure.screen.tall ol.fignotes {
  break-before: avoid; }
.screen-body .panel { margin-bottom: 0; }
.screen-body .panel + .panel { margin-top: 9px; }
figcaption { padding: 7px 12px 5px; font-size: 8.6pt; color: #55636f;
  border-top: 1px solid #e7ecf0; background: #fff; }
.figno { display: inline-block; background: #1f4b6e; color: #fff; font-size: 7pt;
  font-weight: 700; padding: 1px 6px; border-radius: 3px; margin-right: 7px;
  letter-spacing: .04em; }
ol.fignotes { list-style: none; margin: 0; padding: 2px 12px 10px; background: #fff; }
ol.fignotes li { display: flex; gap: 8px; font-size: 8.6pt; margin-bottom: 4px;
  color: #3c4a57; line-height: 1.45; }
.badge { flex: none; width: 15px; height: 15px; border-radius: 50%;
  background: #d4622a; color: #fff; font-size: 7.5pt; font-weight: 700;
  text-align: center; line-height: 15px; margin-top: 1px; }

/* Layout overrides for print.
 * The application uses CSS grid with auto-fit/minmax, which the print renderer
 * does not resolve; every card ends up on its own row and a four-metric panel
 * fills a page. Flexbox gives the same visual result and is fully supported. */
.screen-body .metric-grid { display: flex; flex-wrap: wrap; gap: 7px; }
.screen-body .metric-grid > .metric { flex: 1 1 20%; min-width: 96px; padding: 8px 10px; }
.screen-body .metric-value { font-size: 13pt; }
.screen-body .metric-label { font-size: 6.4pt; }
.screen-body .metric-sub { font-size: 6.4pt; }
.screen-body .manager-cards { display: flex; flex-wrap: wrap; gap: 6px; }
.screen-body .manager-cards > .manager-card { flex: 1 1 30%; min-width: 130px;
  padding: 8px 10px; }
.screen-body .manager-card-figure { font-size: 12pt; }
.screen-body .composition-key { display: flex; flex-wrap: wrap; gap: 2px 10px; }
.screen-body .composition-key li { flex: 1 1 44%; }
.screen-body .indicator-grid { display: flex; flex-wrap: wrap; gap: 6px; }
.screen-body .indicator-grid > .indicator { flex: 1 1 21%; min-width: 92px;
  padding: 7px 9px; }
.screen-body .indicator-value { font-size: 13pt; }
.screen-body .indicator-label { font-size: 6.4pt; }
.screen-body .indicator-expected { font-size: 6pt; }
.screen-body .lock-grid { display: flex; flex-wrap: wrap; gap: 5px; }
.screen-body .lock-grid > .lock-cell { flex: 1 1 22%; }
.screen-body .two-col { display: flex; gap: 10px; }
.screen-body .two-col > * { flex: 1 1 50%; }
.screen-body .chart svg { height: 120px; }
.screen-body .chart-axis span { font-size: 5.6pt; }
.screen-body .change-bars li { font-size: 6.6pt; }
.screen-body .change-label { width: 84px; }
.screen-body .change-value { width: 66px; }
.screen-body .growth-figure { font-size: 15pt; }
.screen-body .panel { padding: 10px 12px; }
.screen-body .gauge-track { height: 16px; }
/* The label column carries a 250px minimum on screen, which is wider than a
 * printed column needs and pushes the month columns off the page. */
.screen-body table.grid .sticky-col { min-width: 0; }
.screen-body table.grid td, .screen-body table.grid th { padding: 3px 6px; }
.screen-body .hint { display: none; }

/* keep app chrome sensible inside a figure */
.screen-body h1 { font-size: 11pt; margin: 0 0 6px; }
.screen-body .table-wrap { overflow: hidden; }
.screen-body table { font-size: inherit; }
"""


# --------------------------------------------------------------------------
# The document
# --------------------------------------------------------------------------

def build(preview: Preview, facts: dict) -> str:
    d = Doc(preview, facts)
    f = facts
    base = f["base"]
    yoy = f["yoy"]

    # ===================================================================
    d.section("1", "What this is, and what it replaces",
              "A short orientation before the screens.")

    d.p("This application replaces the <em>Account Manager Sales Dashboard "
        "FY2025-27</em> workbook. It answers the same questions the workbook "
        "answered &mdash; what has each account manager earned, against what "
        "target, and where is the year heading &mdash; from the same two WinBEAT "
        "exports, but with the calculations held in one place and every figure "
        "traceable back to the transaction that produced it.")

    d.h3("What the workbook could not do")
    d.bullets([
        "<strong>Tell you whether a policy renewed.</strong> The workbook counted "
        "income by transaction type. It could not say that policy 931129338 was "
        "forecast to renew in March and did not.",
        "<strong>Keep a target still.</strong> Budget was recalculated from "
        "whatever was in the sheet at the time, so a target agreed in July could "
        "quietly differ by September.",
        "<strong>Exclude Highview consistently.</strong> Excluded business had to "
        "be filtered by hand in each place it appeared.",
        "<strong>Distinguish 'nothing' from 'not yet' from 'unknown'.</strong> All "
        "three showed as a blank cell or a zero.",
        "<strong>Show its working.</strong> There was no record of who changed a "
        "target, when, or why.",
    ])

    d.h3("What has been built")
    d.table(
        ["Area", "What it provides"],
        [["Reporting", "Fifteen screens covering business, manager, policy and "
                       "data-quality views"],
         ["Forecasting", "A frozen renewal forecast per month, with a full history "
                         "of every forecast ever loaded"],
         ["Budgeting", "Budget = renewal forecast grown by a percentage set per "
                       "manager, per quarter if needed, and lockable"],
         ["Bonus", "Quarterly bonus calculated from the agreed scheme, with a "
                   "tracker showing earned against projected"],
         ["Controls", "Named sign-in, three roles, and an audit record of every "
                      "upload, budget change and sign-in attempt"],
         ["Assurance", "249 automated tests, run from an empty database, "
                       "including a check that the headline position still "
                       "reconciles"]],
    )

    d.callout("A word on the figures in this document",
              "Every screen shown is the live application rendered with the real "
              "book. The reporting cut-off is 31 July 2026, so FY2026-27 is one "
              "month old: most of the year is legitimately empty, and the "
              "interface says so rather than showing zeros.")

    d.page_break()

    # ===================================================================
    d.section("2", "The vocabulary",
              "Six terms carry most of the meaning. Worth agreeing on these "
              "before looking at any screen.")

    d.table(
        ["Term", "Meaning", "Where it comes from"],
        [["<strong>Renewal Forecast</strong>",
          "What the book is expected to renew in a given month.",
          "The Renewals Pending export, frozen when accepted"],
         ["<strong>Growth %</strong>",
          "The uplift expected of a manager on top of their renewal book.",
          "Set per manager, defaulting to 7.5%"],
         ["<strong>Total Budget</strong>",
          "Renewal Forecast + (Renewal Forecast &times; Growth %). The target.",
          "Calculated, then optionally locked"],
         ["<strong>Net Actual Income</strong>",
          "Income earned, after returns. The primary performance measure.",
          "The Sales Transaction export"],
         ["<strong>Latest Outlook</strong>",
          "Actuals for completed months plus forecast for the rest. Where the "
          "year lands if nothing new is written.",
          "Calculated"],
         ["<strong>Remaining Budget Gap</strong>",
          "Budget less Outlook. The work still to find.",
          "Calculated"]],
    )

    d.h3("Three states that are not the same thing")
    d.p("This distinction runs through every screen and is the single most "
        "important thing to understand about the numbers.")
    d.table(
        ["On screen", "Means", "Example"],
        [["<code>$0.00</code>", "A real zero. Nothing was earned.",
          "A manager with no transactions in a month that has closed"],
         ["<code>&mdash;</code>", "The period has not started yet.",
          "March 2027, viewed in August 2026"],
         ["<code>N/A</code>", "We cannot say. Hovering gives the reason.",
          "Achievement where no budget applies"]],
    )
    d.callout("Why this matters",
              "Reporting 0% against a manager whose baseline does not exist says "
              "they failed. The truth is that we cannot measure them. The "
              "workbook could not make that distinction; this does, and it is "
              "enforced by automated tests.", "watch")

    d.h3("Everything is GST inclusive")
    d.p("Stated on every screen and carried into every export, because the source "
        "reports are GST inclusive and a figure quoted without that qualifier "
        "invites a 10% error.")

    d.page_break()

    # ===================================================================
    d.section("3", "How the numbers get in",
              "Two files a month, and a deliberate stop before anything counts.")

    d.steps([
        "<strong>Export from WinBEAT.</strong> The Sales Transaction List and the "
        "Renewals Pending Summary.",
        "<strong>Upload.</strong> The file is staged and read, but no reported "
        "figure moves.",
        "<strong>Review the preview.</strong> Row counts, exclusions, exceptions "
        "and the income totals that <em>will</em> land are shown first.",
        "<strong>Accept.</strong> Only now do the figures change, and only to the "
        "exact numbers previewed.",
        "<strong>Or reject, or roll back.</strong> Both require a reason; rollback "
        "reverses that upload precisely and leaves others untouched.",
    ])

    d.callout("The design principle",
              "Nothing is ever silently dropped. Highview business, unmapped "
              "policy classes and unrecognised managers are all imported in full "
              "and flagged, so an exclusion can be audited and reversed. A record "
              "removed at import is a record nobody can ever ask about again.")

    d.h3("What was reconciled on first load")
    d.table(
        ["Check", "Figure"],
        [["Sales transactions read", "14,886"],
         ["Excluded (Highview)", "2,163"],
         ["Included in reporting", "12,723"],
         ["Positive income", money(f["biz"]["positive_actual_income"])],
         ["Return income", money(f["biz"]["return_income"])],
         ["<strong>Net actual income</strong>",
          f"<strong>{money(f['biz']['net_actual_income'])}</strong>"],
         ["Renewal policies read", "6,749"],
         ["Forecast contribution", "$3,354,995.38"]],
        right={1},
    )
    d.p("Every one of these matched the expected figures exactly on the first "
        "pass. One discrepancy was found and it was in the brief, not the data: "
        "the number of policies with precisely zero expected income is 12, not "
        "11. One policy carries commission and fees that cancel exactly, which a "
        "floating-point calculation reports as a tiny non-zero remainder.")

    d.page_break()

    # ===================================================================
    d.section("4", "Suggested demonstration order",
              "Fifteen minutes, in the order that tells the clearest story.")

    d.table(
        ["", "Screen", "The point to make", "Time"],
        [["1", "Sign in", "Named accounts, recorded attempts, forced password "
                          "change", "1 min"],
         ["2", "Business performance", "Where the whole book stands, and against "
                                       "last year like for like", "3 min"],
         ["3", "Account managers", "One card each; open one", "1 min"],
         ["4", "Account manager detail", "The AM sheet, rebuilt and live", "3 min"],
         ["5", "Bonus tracker", "What the scheme pays, and what is only "
                                "projected", "2 min"],
         ["6", "Policy renewals", "The retention question the workbook could not "
                                  "answer", "2 min"],
         ["7", "Uploads &amp; audit", "The monthly rhythm and the accept step",
          "2 min"],
         ["8", "Settings &amp; mappings", "Maintained without a developer",
          "1 min"]],
        cls="compact",
    )
    d.callout("If time is short",
              "Screens 2, 4 and 5 carry the argument on their own: the business "
              "position, one manager in full, and the money attached to it.")

    d.page_break()
    return d


def add_screens(d: Doc) -> Doc:
    """The page-by-page walkthrough."""
    p, f = d.pv, d.f
    base, yoy = f["base"], f["yoy"]

    # ---------------------------------------------------------------- sign in
    d.section("5", "Sign in",
              "The front door. Nothing behind it is reachable without an account.")
    d.purpose_box(
        "To limit the book, the margins and every manager's performance to named "
        "people, and to keep a record of who saw it and when. Commission data for "
        "the whole brokerage is not something to leave open on a shared link.")

    d.h3("How it works")
    d.bullets([
        "Sign in with your work email address and a password you choose.",
        "The first password is issued by an administrator and <strong>must be "
        "changed on first use</strong> &mdash; an issued password has been written "
        "down somewhere by definition.",
        "Five failed attempts locks the account for fifteen minutes.",
        "A wrong password and an unknown address give the same message, so the "
        "screen cannot be used to discover who has an account.",
        "Sessions last twelve hours, or ninety minutes idle.",
    ])

    d.h3("Roles")
    d.table(
        ["Role", "Can do", "Who has it"],
        [["Viewer", "See every reporting screen, drill down, export",
          "Anastasia"],
         ["Manager", "The same", "&mdash;"],
         ["Administrator", "The above, plus upload and accept data, change budgets "
                           "and growth rates, lock months, maintain mappings, "
                           "manage the bonus scheme",
          "Michael, Sam"]],
    )
    d.callout("Point out in the demo",
              "Every sign-in, failure and lockout is recorded with the time and "
              "address. If someone asks &ldquo;who changed that target?&rdquo; the "
              "answer is on file, not a matter of recollection.")

    d.page_break()

    # ------------------------------------------------------- business
    d.section("6", "Business performance",
              "The whole book on one screen, and the first thing to open.")
    d.purpose_box(
        "To answer three questions immediately: are we ahead or behind, how does "
        "that compare with last year on a fair basis, and where is the difference "
        "coming from. It is the screen for a management meeting rather than a "
        "one-to-one.")

    d.figure(p.panel("business", 0), 
             "The headline comparison, with the verdict banner above it.",
             ["The verdict in plain English before any figure: "
              f"<em>{esc(f['verdict'])}</em>",
              "Earned this year to date &mdash; net actual income for the months "
              "that have closed.",
              "The same period last year. <strong>Cut at the same month</strong>, "
              "so a part year is never compared with a full one.",
              "Growth on prior year, in dollars and percent, coloured by "
              "direction.",
              "Last year in full, for context only. The budget is not derived "
              "from it."])

    d.p("The like-for-like rule matters more than it sounds. FY2026-27 is one "
        "month old. Comparing $322,876 against a full prior year of "
        f"{money(yoy['prior_year_full'])} would show a catastrophic decline that "
        "is purely an artefact of the calendar. Cut at the same month, the "
        f"honest comparison is {money(yoy['ytd_actual'])} against "
        f"{money(yoy['ytd_prior_year'])} &mdash; growth of "
        f"{money(yoy['ytd_growth'])}.")

    d.figure(p.panel("business", 1),
             "Progress against budget, with the period selector.",
             ["The gauge fills to actual against budget; the vertical mark is the "
              "target.",
              "The verdict states over or under, in dollars and percent.",
              "Year to date, each quarter, or the full year &mdash; the figures "
              "below follow the selection.",
              "Achievement is measured against <strong>budget for the months "
              "elapsed</strong>, not the whole period. A quarter one month in is "
              "measured against one month of budget."])

    d.callout("An error worth mentioning, because it was caught",
              "The first version compared one month of actuals with a whole "
              "quarter's budget, and reported every manager at roughly a third of "
              "target. That is arithmetic, not performance. Achievement now runs "
              "on elapsed months only, and the screen shows how many have "
              "elapsed.", "watch")

    d.figure(p.panel("business", 2),
             "Actual against budget by month, with last year as a reference line.",
             ["Bars are actual against budget; the gold line is the same month "
              "last year.",
              "Months that have not started are drawn <strong>absent, not "
              "zero</strong> &mdash; a zero-height bar would say the book earned "
              "nothing.",
              "Clicking a month opens its detail beneath the chart."],
             scale="s90")

    d.figure(p.panel("business", 3),
             "Where the growth is coming from, by account manager.",
             ["Change against the same period last year, largest movement first.",
              "Green to the right is growth, red to the left is decline.",
              "The companion chart beside it splits the same movement by "
              "transaction type."],
             scale="s90")

    top = f["growth_by_manager"]
    d.p("On the current book the movement is concentrated: "
        + ", ".join(f"<strong>{esc(n)}</strong> {money(v)}" for n, v in top[:3])
        + ". That is the sentence a management meeting actually wants, and it "
        "takes one screen rather than an afternoon in the workbook.")

    d.page_break()

    # ------------------------------------------------------- manager index
    d.section("7", "Account managers",
              "One card per manager. The entry point to everything personal.")
    d.purpose_box(
        "To show the whole team at a glance and let you open any one of them. "
        "Earlier versions opened straight onto a single manager, which quietly "
        "implied that person mattered more than the rest.")

    d.figure(trim_rows(p.panel("managerindex", 0), 6, "manager-card"),
             "Six of the fourteen ranked managers, year to date.",
             ["Net actual income year to date, the headline figure per manager.",
              "Budget for the months elapsed, and renewal achievement.",
              "A made or missed verdict with the margin, green or red.",
              "Managers excluded from rankings are marked and can be shown or "
              "hidden."],
             scale="s80", crop=True)

    d.p("Clicking a card opens that manager's full page. The selection is held in "
        "the address, so the page can be linked to or refreshed without losing "
        "the person.")

    d.page_break()

    # ------------------------------------------------------- manager detail
    d.section("8", "Account manager detail",
              "The AM sheet from the workbook, rebuilt and live. The screen for a "
              "one-to-one.")
    d.purpose_box(
        "To hold a performance conversation with a single manager: what they "
        "earned, what they were expected to earn, what target that implies, "
        "whether they made it, and by how much. Deliberately laid out to match "
        "the AM sheets already in use, so the format is familiar.")

    d.figure(p.panel("manager", 0),
             "Where one manager stands, with the year and period selectors.",
             ["Year to date is measured to the reporting cut-off.",
              "Budget for months elapsed, with achievement beneath it.",
              "Latest outlook and the remaining gap for this manager alone.",
              "Prior year in full, for context. The budget is not derived from "
              "it."],
             scale="s90")

    d.h3("The month-by-month grid")
    grid = select_rows(p.panel("manager", 1), [
        "Renewal<", "Transfer Renewal", "Positive Actual Income", "Return Income",
        "Net Actual Income", "Renewal Forecast", "Growth % applied",
        "New Business Growth Target", "Total Budget", "Budget Achievement",
        "Budget Achieved?", "% Above / (Below) Target", "$ Above / (Below) Target"])
    d.figure(trim_cols(grid, 3),
             "The grid, showing the rows that carry the budget chain. Three months "
             "and the year total are shown; the screen carries all twelve.",
             ["Transaction types make up the month.",
              "Return income shown negative and red, because it reduces the "
              "total.",
              "Net Actual Income, the primary measure, emphasised.",
              "Renewal Forecast, then the growth percentage applied to it.",
              "Total Budget, and beneath it the verdict rows: <strong>Budget "
              "Achieved?</strong> in green or red, then the margin in percent and "
              "dollars.",
              "An em dash marks a month that has not started; it is never a "
              "zero."],
             scale="s70")
    d.p("Below the headline figures sits the grid the workbook's AM sheets used: "
        "transaction types down the side, months across the top, and beneath them "
        "the forecast, growth, budget and verdict rows. It is the densest screen "
        "in the application and the one most worth walking through slowly.")

    d.table(
        ["Row", "What it tells you"],
        [["Transaction types",
          "Adjustment, Endorsement, Lapse, New Business, Renewal, Transfer "
          "Renewal and so on &mdash; the composition of the month"],
         ["Positive Actual Income", "Everything earned before returns"],
         ["Return Income",
          "Money that went back out, shown <strong>negative and red</strong> "
          "because it reduces the total"],
         ["<strong>Net Actual Income</strong>",
          "<strong>The primary performance measure</strong>"],
         ["Renewal Forecast", "What was expected to renew that month, frozen"],
         ["Forecast Achievement", "Actual against that forecast"],
         ["Growth % applied",
          "The percentage set for this manager, shown per month"],
         ["New Business Growth Target",
          "Renewal Forecast &times; Growth %, in dollars"],
         ["<strong>Total Budget</strong>",
          "<strong>Renewal Forecast + Growth Target &mdash; the number to "
          "beat</strong>"],
         ["Budget Achievement", "Actual against Total Budget"],
         ["<strong>Budget Achieved?</strong>",
          "<strong>YES in green, NO in red</strong>"],
         ["% Above / (Below) Target", "How far past the target, or short of it"],
         ["$ Above / (Below) Target", "The same in dollars"],
         ["Bonus (indicative)", "Marked indicative; the entitlement is quarterly"],
         ["Prior Year Actual", "The same month last year"],
         ["Renewal Transactions", "A count, not an amount"]],
    )

    d.callout("The line to walk through in the demo",
              "Take Sam Stewart's July 2026. Renewal Forecast $26,516.37, growth "
              "7.5%, so the growth target is $1,988.73 and the budget is "
              "$28,505.10. Actual was $39,699.33. <strong>Budget Achieved? "
              "YES</strong>, +39.3%, $11,194.23 above target. Every step is on the "
              "screen, and $28,505.10 is the same number the workbook has in that "
              "cell.", "good")

    d.h3("Changing a manager's growth percentage")
    d.p("The control sits above the grid, collapsed. It shows the percentage "
        "currently in force, which level of the hierarchy set it, and a chip per "
        "quarter so an uneven year is visible without opening anything.")
    d.bullets([
        "Apply to the whole year, or to a single quarter.",
        "A reason is required, and the change is recorded with your name and the "
        "previous value.",
        "It changes the <strong>budget only</strong>. The renewal forecast is "
        "frozen and is never affected.",
        "It changes <strong>that manager only</strong>. A test asserts nobody "
        "else moves.",
    ])

    d.h3("Locking a month")
    d.p("Once a target has been agreed with a manager it should not drift because "
        "a later Renewals Pending file moved the forecast underneath it. Locking "
        "freezes the month at the figure it holds, storing the whole budget and "
        "its components. Unlocking is a separate, audited act.")

    d.page_break()

    # ------------------------------------------------------- all managers
    d.section("9", "All managers by month",
              "Everybody, every month, one measure at a time.")
    d.purpose_box(
        "To compare the team across the year without opening fourteen pages. Used "
        "for spotting a manager whose month is out of character, or a month where "
        "the whole book dipped.")

    d.figure(trim_rows(p.panel("allmanagers", 0), 7),
             "Net actual income by manager and month (extract).",
             ["Managers down the side, the twelve months of the financial year "
              "across.",
              "One measure at a time &mdash; switchable between Net Actual, Total "
              "Budget, Variance, Budget Achievement and Renewal Forecast.",
              "Row and column totals, with the grand total in the corner.",
              "Variance and achievement cells colour green or red."],
             scale="s70", crop=True)

    d.callout("Why one measure at a time",
              "A matrix showing several measures at once is unreadable, and worse, "
              "invites reading one figure as another. The selector costs a click "
              "and removes a whole class of mistake.")

    d.page_break()

    # ------------------------------------------------------- compare
    d.section("10", "Compare managers",
              "The ranking table, with the full detail behind each row.")
    d.purpose_box(
        "To rank and compare on a chosen period &mdash; monthly, quarterly, year "
        "to date or full year &mdash; with every supporting figure in one row.")

    d.figure(trim_rows(p.panel("managers", 0), 7),
             "The comparison table for Q1 FY2026-27 (extract).",
             ["Compare by month, quarter, year to date or full year.",
              "Quarters are labelled with their financial year, so Q1 is never "
              "ambiguous.",
              "The Result column states Made or Below budget with the margin.",
              "Periods that have not started show an em dash, never a zero.",
              "Started periods sort first, so unstarted quarters do not float to "
              "the top on budget size."],
             scale="s70", crop=True)

    d.page_break()

    # ------------------------------------------------------- forecast history
    d.section("11", "Forecast history",
              "What we expected, and when we expected it.")
    d.purpose_box(
        "To answer &ldquo;what were we forecasting for March, and when did that "
        "change?&rdquo; Each accepted Renewals Pending file adds a row to the "
        "manager's timeline, stamped with when it arrived and who loaded it.")

    d.figure(trim_rows(p.panel("movement", 0), 4),
             "The forecast timeline for one manager.",
             ["Each row is one forecast as it stood on the day it arrived.",
              "Read down a column to see how one month's expectation changed.",
              "Movement between consecutive forecasts is marked with direction "
              "and amount.",
              "The most recent Renewals Pending file is marked current.",
              "A later file only overrides the months it actually covers."],
             scale="s80", crop=True)

    d.callout("What this is for in practice",
              "If a manager's March forecast fell by $40,000 between two uploads, "
              "that is a conversation to have in October, not a surprise in "
              "April. With one snapshot loaded the timeline is short; it becomes "
              "the early-warning screen as files accumulate.")

    d.page_break()

    # ------------------------------------------------------- returns
    d.section("12", "Return income",
              "The money that came back out.")
    d.purpose_box(
        "To show leakage as a category in its own right. Return income is the "
        "difference between what the book wrote and what it kept, and it is large "
        "enough to deserve a screen rather than a footnote.")

    d.figure(p.panel("returns", 0),
             "Positive income, returns, and the resulting net.",
             ["Positive actual income &mdash; everything earned before returns.",
              "Return income, shown as a positive amount because the question is "
              "how much came back out.",
              "Net actual income, the difference.",
              f"The return rate: <strong>{float(f['return_rate'])*100:.1f}%</strong> "
              "of everything earned came back out."],
             scale="s90")

    d.figure(p.panel("returns", 1),
             "What the returns were made of.",
             ["Each segment is a category, sized by share.",
              "Clicking a segment isolates it in the table below.",
              "Lapses dominate, which is the useful finding."],
             scale="s90")

    d.p("Across the loaded book, returns total "
        f"{money(f['return_total'])}. The largest single category is "
        f"<strong>{esc(f['return_top'][0][0])}</strong> at "
        f"{money(f['return_top'][0][1])} &mdash; more than half. That is a "
        "retention number wearing a finance label, and it is the strongest "
        "argument for the policy-level renewal tracking on the next screen.")

    d.callout("A simplification worth noting",
              "This screen originally showed signed and absolute columns side by "
              "side. They were the same number with the sign flipped. The second "
              "column has been replaced with each category's share of positive "
              "income, which says something the first did not.")

    d.page_break()

    # ------------------------------------------------------- policies
    d.section("13", "Policy renewals",
              "The retention list. The question the workbook could not answer.")
    d.purpose_box(
        "To show every policy the book was forecast to renew and what actually "
        "happened to it &mdash; renewed, transferred, lapsed, or still pending. "
        "Filter by outcome and it becomes a chase list.")

    d.figure(trim_rows(p.panel("policies", 0), 6),
             "Policy-level renewal outcomes.",
             ["Every forecast policy, with its client, class and expected income.",
              "The outcome, once known: renewed, transferred, lapsed or pending.",
              "Expected income against what was actually earned.",
              "Filterable by outcome, manager, class and month."],
             scale="s80", crop=True)

    d.callout("Being straight about the current state",
              "Only two forecast policies fall inside the reporting window today, "
              "because the Renewals Pending file was taken after most July "
              "renewals had already transacted. This screen fills out from the "
              "next export onward. The machinery is built and tested; it is "
              "waiting for data, not for development.", "watch")

    d.page_break()

    # ------------------------------------------------------- review
    d.section("14", "Matching review",
              "Linking each forecast policy to the transaction that renewed it.")
    d.purpose_box(
        "The two source files share no common key: Renewals Pending has a "
        "PolicyID, Sales Transactions does not. Matching links them on client "
        "code, policy number, class and date, so renewal performance can be "
        "measured policy by policy. Most links are made automatically; this queue "
        "holds only what the system will not decide alone.")

    d.h3("What lands in the queue")
    d.bullets([
        "One transaction that could belong to two different forecast policies. "
        "Distinct PolicyIDs legitimately share a client and policy number, so "
        "nothing is credited automatically &mdash; crediting the wrong one would "
        "inflate a manager's renewal achievement.",
        "Weak matches, on client and class but no policy number, that need a "
        "human eye.",
        "Renewals with no corresponding forecast policy at all.",
    ])
    d.p("Any decision is recorded with your name, the reason, and what it "
        "replaced. No transaction can ever be credited to two policies; that is "
        "enforced in the database rather than trusted to the matcher.")

    d.callout("Also being straight here",
              "There are four actionable items today. The page separates them "
              "from 584 July timing artefacts and 8,071 prior-year transactions "
              "that are out of scope, because listing all three together would "
              "bury the real decisions. Like Policy renewals, this becomes useful "
              "with the next Renewals Pending file.", "watch")

    d.page_break()

    # ------------------------------------------------------- budget
    d.section("15", "Budget",
              "Where targets are set for everyone at once.")
    d.purpose_box(
        "To set and review growth rates across the business, see the resulting "
        "budget by manager and quarter, and keep a record of every change. The "
        "per-manager control on an individual's page covers the common case; this "
        "screen is for setting a business-wide default or reviewing them all "
        "together.")

    d.figure(trim_rows(p.panel("budget", 0), 8),
             "Budget by manager and quarter (extract).",
             ["Renewal forecast, growth basis and percentage, growth target and "
              "total budget.",
              "The growth basis names which rule applied: global, manager, or "
              "manager and quarter.",
              "A quarter whose months disagree reports <em>mixed</em> rather than "
              "claiming a single rate it does not have.",
              "Changes require a reason and are audited."],
             scale="s70", crop=True)

    d.callout("A trap that was removed",
              "This form used to default to Global scope while still showing a "
              "manager field. Naming a manager there was silently ignored and the "
              "change applied to everybody. The API now refuses a contradictory "
              "instruction outright, so no interface can repeat it.", "watch")

    d.page_break()

    # ------------------------------------------------------- bonus
    d.section("16", "Bonus tracker",
              "What the scheme pays, and what is only a projection.")
    d.purpose_box(
        "To calculate the quarterly bonus from the agreed scheme and show each "
        "manager where they stand against it. The scheme rewards clearing the "
        "budget target, with a share of everything above it.")

    d.h3("The scheme")
    d.table(
        ["Step", "Calculation"],
        [["Budget Target", "Expected Income &times; (1 + Growth %)"],
         ["Base Bonus", "(Budget Target &minus; Expected Income) &divide; 3"],
         ["Above-Target Bonus", "(Actual Income &minus; Budget Target) &times; 20%"],
         ["<strong>Total</strong>",
          "<strong>Nil below target; otherwise Base + Above-Target</strong>"]],
    )
    d.p("The divisor and the 20% are settings, not fixed constants, so changing "
        "the scheme is an administrator's decision recorded with a reason rather "
        "than a change request.")

    d.figure(p.panel("bonus", 0),
             "The position across the business.",
             ["Earned to date &mdash; what would pay on the figures so far.",
              "Projected for quarters under way, at the pace of completed months.",
              "Full year at target: what the scheme costs if every manager hits "
              "target exactly.",
              "Full year outlook: projection for started quarters plus base bonus "
              "for the rest."],
             scale="s90")

    bonus = f["bonus"]
    d.table(
        ["Measure", "Amount", "What it means"],
        [["Earned to date", money(bonus["earned_bonus"]),
          "Correctly nil: no quarter has closed"],
         ["Projected, quarters under way", money(bonus["projected_bonus"]),
          "At the pace of one completed month. Not money earned"],
         ["Full year at target", money(bonus["bonus_at_target"]),
          "The cost of the scheme if every target is met exactly"],
         ["Full year outlook", money(bonus["full_year_outlook"]),
          "Projection plus base bonus on quarters not yet begun"]],
        right={1},
    )

    d.callout("The distinction to be firm about",
              "Earned and projected are kept apart everywhere, and each column "
              "states the period it covers. A projection for one quarter can "
              "legitimately exceed a full year's base bonus, which looks like an "
              "error until the scopes are stated. Putting a projection under a "
              "heading that reads <em>bonus</em> would show a manager money that "
              "is not money.", "watch")

    d.figure(trim_rows(p.panel("bonus", 2), 8),
             "Quarterly bonus position, by manager (extract).",
             ["Expected income, growth percentage, growth target and budget "
              "target.",
              "Actual, and the amount above or below.",
              "Income still required before any bonus is payable at all.",
              "A status of Earned, Missed, On track, Behind or Not started.",
              "A quarter that has not started carries no bonus figure &mdash; not "
              "a nil."],
             scale="s70", crop=True)

    d.h3("The monthly bonus row is indicative")
    d.p("Each manager's grid carries a bonus row, marked indicative. The "
        "entitlement is quarterly, and monthly figures deliberately do not sum to "
        "it: a quarter can be missed overall while individual months within it ran "
        "ahead. Both are shown, and only the quarterly figure pays.")

    d.page_break()

    # ------------------------------------------------------- data quality
    d.section("17", "Data quality",
              "The screen that says whether to trust the others.")
    d.purpose_box(
        "To surface every reconciliation check, exception and unmapped value in "
        "one place, with a drill-down to the underlying records. If this screen "
        "is clean, the reported figures are sound.")

    d.figure(trim_rows(p.panel("dq", 0), 9, "indicator"),
             "Reconciliation and exception counts (extract).",
             ["Each indicator shows the count found against the count expected.",
              "A tick means the figure reconciles exactly.",
              "Clicking an indicator lists the records behind it.",
              "The twelve zero expected-income policies appear here, with the "
              "expected figure alongside."],
             scale="s90", crop=True)

    d.figure(p.panel("dq", 1),
             "Forecast baselines, and what each month is measured against.",
             ["Each month states its baseline source.",
              "July 2026 uses supplied per-manager figures, because the Renewals "
              "Pending export was taken after most July renewals had transacted.",
              "Any manager without a baseline in a month would be listed here as "
              "an exception."],
             scale="s90")

    d.page_break()

    # ------------------------------------------------------- uploads
    d.section("18", "Uploads and audit",
              "The monthly rhythm, and the record of everything ever loaded.")
    d.purpose_box(
        "To bring new data in safely, and to keep a permanent record of what was "
        "loaded, by whom, when, and what it changed. The two-phase design means "
        "no upload can surprise you.")

    d.figure(trim_rows(p.panel("uploads", 0), 6),
             "Batch history, with the figures each upload contributed.",
             ["Every batch: file, type, status, row counts and net income.",
              "The coverage period each file spans.",
              "Who uploaded it and when.",
              "A pending batch can be accepted or rejected from here at any time.",
              "An accepted batch can be rolled back, with a reason."],
             scale="s80", crop=True)

    d.h3("The monthly routine")
    d.steps([
        "Export both reports from WinBEAT.",
        "Upload the Sales Transaction List. Check the preview: row count, "
        "exclusions, net income.",
        "Accept it if the figures are right.",
        "Upload the Renewals Pending Summary. Confirm the months it covers.",
        "Accept.",
        "Move the reporting cut-off date forward on the Settings screen.",
        "Glance at Data quality. If it is clean, the month is closed.",
    ])
    d.callout("What makes this safe",
              "The preview shows the exact figures that will land. Acceptance uses "
              "those same figures &mdash; a test asserts it, so the preview can "
              "never differ from the result. Nothing is irreversible.", "good")

    d.page_break()

    # ------------------------------------------------------- settings
    d.section("19", "Settings and mappings",
              "The parts that need maintaining as the business changes.")
    d.purpose_box(
        "To keep the routine changes &mdash; the reporting cut-off, manager name "
        "mappings, policy class equivalences &mdash; in the hands of an "
        "administrator rather than a developer. These accumulate with every "
        "insurer export, and a system that needs a code change for each one "
        "gradually stops being accurate.")

    d.figure(trim_rows(p.panel("settings", 1), 7),
             "Policy classes awaiting a mapping (extract of 73).",
             ["The two source systems use different class vocabularies.",
              "An unmapped class still matches on client and policy number, but "
              "cannot reach the top matching tier.",
              "Map one in a few seconds; the list shrinks.",
              "73 are outstanding today &mdash; visible, rather than quietly "
              "degrading match quality."],
             scale="s80", crop=True)

    d.h3("Also on this screen")
    d.bullets([
        "<strong>Reporting cut-off date.</strong> The line between completed and "
        "future periods. Moving it backwards past months that already hold "
        "transactions is refused, because that would hide real income.",
        "<strong>Manager aliases.</strong> Source names mapped to reporting "
        "managers. Applied at read time, so adding one corrects history as well "
        "as new records.",
        "<strong>Exclusion rules.</strong> Highview and others. Deactivating a "
        "rule brings its records back into reported totals &mdash; nothing was "
        "deleted.",
        "<strong>Transaction categories.</strong> An unknown category is never "
        "guessed at; it classifies as Unmapped and appears in Data quality.",
    ])

    d.page_break()

    # ------------------------------------------------------- position
    d.section("20", "The current position",
              "Where the book stands as at the reporting cut-off, 31 July 2026.")

    d.table(
        ["Measure", "Amount"],
        [["FY2026-27 Renewal Forecast", money(base["original_renewal_forecast"])],
         ["Total Budget at 7.5% growth", money(base["total_budget"])],
         ["Latest Outlook", money(base["latest_outlook"])],
         ["<strong>Remaining Budget Gap</strong>",
          f"<strong>{money(base['remaining_budget_gap'])}</strong>"],
         ["Year to date actual", money(yoy["ytd_actual"])],
         ["Same period last year", money(yoy["ytd_prior_year"])],
         ["Growth on prior year", money(yoy["ytd_growth"])]],
        right={1},
    )

    d.p("The outlook is deliberately conservative: it assumes <strong>no new "
        "business at all</strong> for the rest of the year. New business only "
        "enters the figure once it appears in a Sales Transaction report. The "
        f"gap of {money(base['remaining_budget_gap'])} is therefore the size of "
        "the job, not a prediction of a shortfall.")

    d.h3("Manager position, year to date")
    d.table(
        ["Manager", "Net actual", "Result", "Margin"],
        [[esc(n), money(v), r, f"{m:+.1f}%" if m is not None else "&mdash;"]
         for n, v, r, m in f["top"]],
        right={1, 3},
    )

    d.page_break()

    # ------------------------------------------------------- assurance
    d.section("21", "How far the figures can be trusted",
              "What has been verified, and what has not.")

    d.h3("What is checked automatically")
    d.bullets([
        "<strong>249 tests</strong>, run from an empty database on every change, "
        "covering reconciliation, budget arithmetic, bonus calculation, matching "
        "integrity, permissions and authentication.",
        "The headline position is asserted on every run. If the forecast, budget, "
        "outlook or gap moves without an intended cause, the tests fail.",
        "Every summary figure is checked against its own drill-down, so a total "
        "and its detail cannot disagree.",
        "Exports are checked against the filtered screen they came from.",
        "A dedicated set of tests asserts that unavailable measures never render "
        "as zero.",
    ])

    d.h3("Design choices that protect the numbers")
    d.table(
        ["Choice", "Why"],
        [["Every financial calculation lives in the database",
          "One place to fix a figure; the interface only formats"],
         ["No figure is ever computed in the browser",
          "A total summed from a visible page understates the moment paging "
          "begins"],
         ["Money held as exact decimal, never floating point",
          "Cents do not drift"],
         ["No AI or external service in any calculation",
          "The same input always produces the same number, and every rule can be "
          "pointed at during a review"],
         ["The Original Forecast is frozen when accepted",
          "A target cannot be rewritten by a later upload"]],
    )

    d.h3("What is not yet proven")
    d.bullets([
        "<strong>Matching accuracy.</strong> It cannot be measured until a "
        "forecast period overlaps transacted actuals. The next Renewals Pending "
        "export supplies that.",
        "<strong>Policy-level retention.</strong> Two policies are in scope today. "
        "The screens are built and tested; they need data.",
        "<strong>Bonus on a closed quarter.</strong> No quarter has closed, so no "
        "bonus has been settled. The calculation is tested, including above-target "
        "cases, but has not yet paid anything.",
        "<strong>The bonus basis.</strong> It currently runs on Net Actual Income, "
        "so lapses and cancellations reduce it. If the scheme is meant to run on "
        "positive income only, that is a one-line change &mdash; but it materially "
        "affects who earns, and should be decided deliberately.",
    ])

    d.callout("The honest summary",
              "The reporting, budgeting and bonus calculations are complete, "
              "reconciled and tested against the real book. The retention and "
              "matching features are built but data-starved, and become useful "
              "from the next monthly export onward.", "good")

    return d


def main() -> int:
    preview = Preview(Path(sys.argv[1]))
    facts = json.loads(Path(sys.argv[2]).read_text())
    out = Path(sys.argv[3])

    d = add_screens(build(preview, facts))

    html_out = out.with_suffix(".html")
    html_out.write_text(d.render())

    from weasyprint import HTML
    HTML(str(html_out)).write_pdf(str(out))
    print(f"wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
