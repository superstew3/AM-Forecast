import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, cell, cellTitle, money, monthAU, percent, } from "../lib/api";
import { BudgetGauge, MonthlyBars } from "../components/charts";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { GrowthControl } from "../components/GrowthControl";
import { MonthLocks } from "../components/MonthLocks";
import { Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";
/** How a row should be read. Declared by the API, not guessed from the label. */
function kindOf(row) {
    return row.value_kind ?? "money";
}
/** Green for made, red for missed, and for the margin either side of budget. */
function cellTone(row, c) {
    if (c.status !== "actual" || c.value === null)
        return "";
    if (row.value_kind === "verdict") {
        return Number(c.value) >= 1 ? " cell-yes" : " cell-no";
    }
    if (row.label.startsWith("% Above")) {
        return Number(c.value) >= 0 ? " cell-good" : " cell-bad";
    }
    return Number(c.value) < 0 ? " negative" : "";
}
function rowClass(row) {
    return `grid-row grid-${row.kind}`;
}
export default function ManagerDetail() {
    // The manager lives in the URL, so a link opens the right person and a
    // refresh keeps them.
    const [searchParams, setSearchParams] = useSearchParams();
    const manager = searchParams.get("name") ?? "";
    const setManager = (name) => setSearchParams({ name }, { replace: false });
    const { years, currentFy } = usePeriods();
    const [fyPick, setFyPick] = useState(null);
    const fy = fyPick ?? currentFy ?? new Date().getFullYear();
    const setFy = setFyPick;
    const [view, setView] = useState("month");
    const ref = useQuery({ queryKey: ["reference"], queryFn: api.reference });
    const yoy = useQuery({
        queryKey: ["yoy-mgr", manager, fy],
        queryFn: () => api.yearOverYear(fy, manager),
    });
    const budget = useQuery({ queryKey: ["budget", fy], queryFn: () => api.budget(fy) });
    const monthlyBudget = (budget.data?.monthly ?? [])
        .filter((r) => r.canonical_manager === manager);
    const lockedMonths = monthlyBudget.filter((r) => r.is_locked);
    const q = useQuery({
        queryKey: ["manager-detail", manager, fy],
        queryFn: () => api.managerDetail(manager, fy),
    });
    if (!manager) {
        return (_jsxs("div", { className: "state empty", children: ["No manager selected. Choose one from ", _jsx("a", { href: "/managers-index", children: "Account managers" }), "."] }));
    }
    if (q.isLoading)
        return _jsx(Loading, { what: "manager detail" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    const params = new URLSearchParams({ manager, financial_year: String(fy) });
    const picker = (_jsxs("div", { className: "controls", children: [_jsxs("label", { children: ["Account manager", _jsx("select", { value: manager, onChange: (e) => setManager(e.target.value), children: (ref.data?.managers ?? []).map((m) => (_jsxs("option", { value: m.canonical_manager, children: [m.canonical_manager, m.include_in_rankings ? "" : " (not ranked)"] }, m.canonical_manager))) })] }), _jsxs("label", { children: ["Financial year", _jsx("select", { value: fy, onChange: (e) => setFy(Number(e.target.value)), children: _jsx(YearOptions, { years: years }) })] }), _jsxs("div", { className: "segmented", children: [_jsx("button", { className: view === "month" ? "on" : "", onClick: () => setView("month"), children: "Monthly" }), _jsx("button", { className: view === "quarter" ? "on" : "", onClick: () => setView("quarter"), children: "Quarterly" })] }), _jsx("a", { className: "button", href: api.exportUrl("transactions", "xlsx", params), children: "Export transactions" })] }));
    return (_jsxs(_Fragment, { children: [_jsxs("div", { className: "crumb", children: [_jsx("a", { href: "/managers-index", children: "Account managers" }), " ", _jsx("span", { children: "/" }), " ", d.canonical_manager] }), _jsxs("h1", { children: [d.canonical_manager, _jsx("span", { className: "fy", children: d.financial_year_label }), !d.include_in_rankings && _jsx("span", { className: "chip", children: "not ranked" })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Notes, { notes: d.meta.notes }), yoy.data && (_jsx("div", { className: `verdict-bar ${yoy.data.on_track === null ? "neutral"
                    : yoy.data.on_track ? "good" : "bad"}`, children: _jsxs("strong", { children: [d.canonical_manager, ": ", yoy.data.verdict] }) })), _jsx(Panel, { title: "Where this manager stands", subtitle: `Year to date is measured to the reporting cut-off, ${d.cut_off_month}. Months after that have not started.`, actions: picker, children: _jsxs("div", { className: "metric-grid", children: [_jsx(Metric, { label: "Year-to-date Actual", m: d.ytd_actual, emphasis: true }), _jsx(Metric, { label: "Year-to-date Budget", m: d.ytd_budget, ratio: d.ytd_achievement, hint: "Budget for the months completed so far, not the full year." }), _jsx(Metric, { label: "Full-year Budget", m: d.full_year_budget, hint: "Original Renewal Forecast plus the new business growth target." }), _jsx(Metric, { label: "Latest Outlook", m: d.latest_outlook, hint: "Actuals for completed months plus Latest Forecast for the rest. No assumed new business." }), _jsx(Metric, { label: "Remaining Budget Gap", m: d.remaining_budget_gap, hint: "Still to be found through new business, retention or other actual activity." }), _jsx(Metric, { label: "Prior Year Actual", m: d.prior_year_actual, hint: "Full prior financial year, for comparison only. The budget is not derived from it." })] }) }), _jsx(GrowthControl, { manager: d.canonical_manager, financialYear: fy, activePct: d.active_growth_pct, activeBasis: d.active_growth_basis, quarterGrowth: d.quarter_growth ?? [] }), _jsx(Panel, { title: "Against budget", subtitle: "Measured on this manager's own growth percentage, over the months completed.", children: _jsx(BudgetGauge, { actual: d.ytd_actual?.value != null ? Number(d.ytd_actual.value) : null, budget: d.ytd_budget?.value != null ? Number(d.ytd_budget.value) : null, label: "year-to-date budget" }) }), yoy.data && (_jsx(Panel, { title: "Month by month against budget", subtitle: "Bars are actual against budget; the line is the same month last year.", children: _jsx(MonthlyBars, { data: yoy.data.months.map((m) => ({
                        label: m.label,
                        actual: m.net_actual != null ? Number(m.net_actual) : null,
                        budget: m.budget != null ? Number(m.budget) : null,
                        prior: m.prior_year_actual != null ? Number(m.prior_year_actual) : null,
                        started: m.started,
                    })) }) })), view === "quarter" ? (_jsx(Panel, { title: "By quarter", subtitle: "A quarter that has not started shows no actual figures rather than zero.", children: _jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { children: "Quarter" }), _jsx("th", { className: "right", children: "Original Forecast" }), _jsx("th", { className: "right", children: "Total Budget" }), _jsx("th", { className: "right", children: "Net Actual" }), _jsx("th", { className: "right", children: "Variance" }), _jsx("th", { className: "right", children: "Achievement" })] }) }), _jsx("tbody", { children: d.quarters.map((qr) => (_jsxs("tr", { children: [_jsxs("td", { children: ["Q", qr.quarter, !qr.started && _jsx("span", { className: "chip", children: "not started" })] }), _jsx("td", { className: "right", children: money({ value: qr.original_forecast, available: qr.original_forecast !== null }) }), _jsx("td", { className: "right", children: money({ value: qr.total_budget, available: qr.total_budget !== null }) }), _jsx("td", { className: "right", children: qr.started
                                                ? money({ value: qr.net_actual_income, available: qr.net_actual_income !== null })
                                                : _jsx("span", { className: "not-yet", title: "This quarter has not started yet.", children: "\u2014" }) }), _jsx("td", { className: "right", children: qr.variance !== null
                                                ? money({ value: qr.variance, available: true })
                                                : _jsx("span", { className: "not-yet", title: "This quarter has not started yet.", children: "\u2014" }) }), _jsx("td", { className: "right", children: qr.achievement !== null
                                                ? percent({ value: qr.achievement, available: true })
                                                : _jsx("span", { className: "not-yet", title: "This quarter has not started yet.", children: "\u2014" }) })] }, qr.quarter))) })] }) }) })) : (_jsx(Panel, { title: "Month by month", subtitle: "Transaction types, then the forecast and budget they are measured against. An em dash means the month has not started; N/A means the measure is unavailable and the tooltip says why.", children: _jsx("div", { className: "table-wrap", children: _jsxs("table", { className: "grid", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { className: "sticky-col", children: "Transaction type / measure" }), d.months.map((m, i) => (_jsx("th", { className: `right${d.month_status[i] === "future" ? " future-col" : ""}`, children: monthAU(m) }, m))), _jsx("th", { className: "right total-col", children: "Total" })] }) }), _jsx("tbody", { children: d.rows.map((row) => {
                                    const k = kindOf(row);
                                    return (_jsxs("tr", { className: rowClass(row), children: [_jsxs("td", { className: "sticky-col", children: [row.label, row.hint && _jsx("span", { className: "hint", title: row.hint, children: "i" })] }), row.cells.map((c, i) => (_jsx("td", { className: `right${d.month_status[i] === "future" ? " future-col" : ""}${c.status === "future" ? " not-yet" : ""}${c.status === "unavailable" ? " na-cell" : ""}${cellTone(row, c)}`, title: cellTitle(c), children: cell(c, k) }, c.month))), _jsx("td", { className: "right total-col", children: row.total === null
                                                    ? (k === "percent" || k === "verdict" ? "" : "—")
                                                    : k === "count"
                                                        ? String(Math.round(Number(row.total)))
                                                        : k === "percent"
                                                            ? percent({ value: row.total, available: true })
                                                            : money({ value: row.total, available: true }) })] }, row.label));
                                }) })] }) }) })), _jsxs(Panel, { title: "Locked months", subtitle: "A locked month keeps the budget figure it held when locked, and stops moving even if the forecast beneath it changes. That is what makes a target safe to agree with a manager.", children: [_jsx(MonthLocks, { manager: d.canonical_manager, months: monthlyBudget }), lockedMonths.length === 0 && (_jsxs("p", { className: "footnote", children: ["No months are locked for ", d.canonical_manager, "."] }))] })] }));
}
