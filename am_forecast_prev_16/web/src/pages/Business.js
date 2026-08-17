import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { api } from "../lib/api";
import { BudgetGauge, ChangeBars, MonthlyBars } from "../components/charts";
import { BaselineWarning, Failed, GstBanner, Loading, Metric, Notes, Panel, Value } from "../components/ui";
export default function Business() {
    const { years, currentFy } = usePeriods();
    const [fyPick, setFyPick] = useState(null);
    // Default to the current financial year once the data says what it is.
    const fy = fyPick ?? currentFy ?? new Date().getFullYear();
    const setFy = setFyPick;
    const [pick, setPick] = useState(null);
    const [scope, setScope] = useState("ytd");
    const biz = useQuery({ queryKey: ["business", fy], queryFn: () => api.business(fy) });
    const yoy = useQuery({ queryKey: ["yoy", fy], queryFn: () => api.yearOverYear(fy) });
    if (biz.isLoading || yoy.isLoading)
        return _jsx(Loading, { what: "business performance" });
    if (biz.isError)
        return _jsx(Failed, { error: biz.error, retry: () => biz.refetch() });
    if (yoy.isError)
        return _jsx(Failed, { error: yoy.error, retry: () => yoy.refetch() });
    const d = biz.data;
    const y = yoy.data;
    const series = y.months.map((m) => ({
        label: m.label,
        actual: m.net_actual != null ? Number(m.net_actual) : null,
        budget: m.budget != null ? Number(m.budget) : null,
        prior: m.prior_year_actual != null ? Number(m.prior_year_actual) : null,
        started: m.started,
    }));
    const growthUp = Number(y.ytd_growth?.value ?? 0) >= 0;
    /** Which months the selected scope covers. */
    const inScope = (i) => {
        if (scope === "year")
            return true;
        if (scope === "ytd")
            return y.months[i].started;
        const q = Number(scope[1]);
        return i >= (q - 1) * 3 && i < q * 3;
    };
    const scopedMonths = y.months.filter((_, i) => inScope(i));
    const sum = (key) => scopedMonths.reduce((t, m) => m[key] != null ? t + Number(m[key]) : t, 0);
    const anyStarted = scopedMonths.some((m) => m.started);
    const scopeActual = anyStarted ? sum("net_actual") : null;
    const scopeBudget = scopedMonths.reduce((t, m) => (m.budget != null && (scope === "year" || m.started)) ? t + Number(m.budget) : t, 0);
    const scopePrior = sum("prior_year_actual");
    const scopeLabel = scope === "ytd" ? "Year to date"
        : scope === "year" ? "Full year" : `Q${scope[1]} ${y.label}`;
    const SCOPES = [
        { key: "ytd", label: "Year to date" },
        { key: "q1", label: "Q1 Jul-Sep" },
        { key: "q2", label: "Q2 Oct-Dec" },
        { key: "q3", label: "Q3 Jan-Mar" },
        { key: "q4", label: "Q4 Apr-Jun" },
        { key: "year", label: "Full year" },
    ];
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Business performance", _jsx("span", { className: "fy", children: y.label }), _jsx("select", { className: "inline-select", value: fy, onChange: (e) => setFy(Number(e.target.value)), children: _jsx(YearOptions, { years: years }) })] }), _jsx(GstBanner, { meta: d.meta }), _jsx("div", { className: `verdict-bar ${y.on_track === null ? "neutral" : y.on_track ? "good" : "bad"}`, children: _jsx("strong", { children: y.verdict }) }), _jsx(Panel, { title: "This year against last", subtitle: `Like for like: ${y.prior_label} is cut at the same month of the year as the current reporting cut-off, so a part year is never compared with a full one.`, children: _jsxs("div", { className: "metric-grid", children: [_jsx(Metric, { label: "Earned this year to date", m: y.ytd_actual, emphasis: true }), _jsx(Metric, { label: `Same period ${y.prior_label}`, m: y.ytd_prior_year }), _jsxs("div", { className: `metric metric-emphasis tone-${growthUp ? "good" : "bad"}`, children: [_jsx("div", { className: "metric-label", children: "Growth on prior year" }), _jsxs("div", { className: "metric-value", children: [growthUp ? "+" : "", _jsx(Value, { m: y.ytd_growth })] }), _jsxs("div", { className: "metric-sub", children: [growthUp ? "up " : "down ", _jsx(Value, { m: y.ytd_growth_pct, kind: "percent" }), " on the same period"] })] }), _jsx(Metric, { label: `${y.prior_label} full year`, m: y.prior_year_full, hint: "The whole prior year, for context. The budget is not derived from it." })] }) }), _jsxs(Panel, { title: `Against budget — ${scopeLabel}`, subtitle: "Budget is the renewal forecast plus the new business growth target. Achievement is measured only on months that have started.", actions: _jsx("div", { className: "segmented", children: SCOPES.map((sc) => (_jsx("button", { className: scope === sc.key ? "on" : "", onClick: () => setScope(sc.key), children: sc.label }, sc.key))) }), children: [_jsx(BudgetGauge, { actual: scopeActual, budget: anyStarted ? scopeBudget : null, label: `${scopeLabel.toLowerCase()} budget` }), _jsxs("div", { className: "metric-grid", style: { marginTop: 14 }, children: [_jsx(Metric, { label: `${scopeLabel} Actual`, m: { value: scopeActual, available: scopeActual !== null,
                                    reason: "This period has not started yet." }, emphasis: true }), _jsx(Metric, { label: `${scopeLabel} Budget`, m: { value: anyStarted ? scopeBudget : null, available: anyStarted,
                                    reason: "This period has not started yet." } }), _jsx(Metric, { label: `${scopeLabel} last year`, m: { value: scopePrior || null, available: scopePrior !== 0,
                                    reason: "No prior-year figure for this period." } })] }), _jsxs("div", { className: "metric-grid", style: { marginTop: 14 }, children: [_jsx(Metric, { label: "Year-to-date Budget", m: y.ytd_budget }), _jsx(Metric, { label: "Variance to Budget", m: y.ytd_variance }), _jsx(Metric, { label: "Full-year Budget", m: y.full_year_budget }), _jsx(Metric, { label: "Latest Outlook", m: y.latest_outlook, hint: "Actuals for completed months plus Latest Forecast for the rest. No assumed new business." }), _jsx(Metric, { label: "Remaining Budget Gap", m: y.remaining_gap, hint: "Still to be found through new business, retention or other actual activity." }), _jsx(Metric, { label: "Outlook vs prior year", m: y.outlook_vs_prior_year_pct, kind: "percent", hint: "Where the full year is heading against last year's actual result." })] })] }), _jsxs(Panel, { title: "Month by month", subtitle: "Bars are actual against budget; the line is the same month last year. Months that have not started are left empty rather than drawn as zero.", children: [_jsx(MonthlyBars, { data: series, onSelect: setPick, selected: pick }), pick && (() => {
                        const m = y.months.find((x) => x.label === pick);
                        if (!m)
                            return null;
                        return (_jsxs("div", { className: "month-detail", children: [_jsx("strong", { children: pick }), _jsxs("span", { children: ["Actual ", _jsx(Value, { m: { value: m.net_actual,
                                                available: m.net_actual !== null,
                                                reason: "This month has not started yet." } })] }), _jsxs("span", { children: ["Budget ", _jsx(Value, { m: { value: m.budget, available: m.budget !== null } })] }), _jsxs("span", { children: ["Variance ", _jsx(Value, { m: { value: m.variance_to_budget,
                                                available: m.variance_to_budget !== null,
                                                reason: "This month has not started yet." } })] }), _jsxs("span", { children: ["Achievement ", _jsx(Value, { m: { value: m.achievement,
                                                available: m.achievement !== null,
                                                reason: "This month has not started yet." }, kind: "percent" })] }), _jsxs("span", { children: ["Last year ", _jsx(Value, { m: { value: m.prior_year_actual,
                                                available: m.prior_year_actual !== null,
                                                reason: "No prior-year figure." } })] }), _jsx("button", { onClick: () => setPick(null), children: "Clear" })] }));
                    })()] }), _jsxs("div", { className: "two-col", children: [_jsx(Panel, { title: "Where the growth is coming from", subtitle: "Change on the same period last year, by account manager.", children: _jsx(ChangeBars, { items: y.growth_by_manager.map((r) => ({
                                label: r.canonical_manager, change: Number(r.change),
                            })) }) }), _jsx(Panel, { title: "By transaction type", subtitle: "Which kinds of business moved.", children: _jsx(ChangeBars, { items: y.growth_by_type.map((r) => ({
                                label: r.classification, change: Number(r.change),
                            })) }) })] }), _jsx(Panel, { title: "Income and leakage", children: _jsxs("div", { className: "metric-grid", children: [_jsx(Metric, { label: "Positive Actual Income", m: d.positive_actual_income }), _jsx(Metric, { label: "Return Income", m: d.return_income, hint: "Money that came back out. Reduces Net Actual Income." }), _jsx(Metric, { label: "Net Actual Income", m: d.net_actual_income, emphasis: true }), _jsx(Metric, { label: "Actual New Business", m: d.actual_new_business, hint: "Recognised only once it appears in Sales Transactions." }), _jsx(Metric, { label: "Lapse / Lost Renewal", m: d.lapse_return_income }), _jsx(Metric, { label: "Mid-Term Cancellation", m: d.midterm_cancellation_return_income }), _jsx(Metric, { label: "New Business Cancellation", m: d.new_business_cancellation_return_income }), _jsx(Metric, { label: "Negative Endorsements", m: d.negative_endorsements })] }) }), _jsx(Panel, { title: "Renewal forecast", subtitle: "The forecast the budget is built on. Completed months keep the figure they were measured against; future months update when a newer Renewals Pending file is loaded.", children: _jsxs("div", { className: "metric-grid", children: [_jsx(Metric, { label: "Renewal Forecast", m: d.original_renewal_forecast }), _jsx(Metric, { label: "Total Budget", m: d.total_budget, hint: "Renewal Forecast plus the new business growth target." }), _jsx(Metric, { label: "Latest Outlook", m: d.latest_outlook })] }) }), _jsx(Notes, { notes: d.meta.notes }), fy === 2026 && (_jsx(BaselineWarning, { month: "July 2026", source: "A renewal forecast per manager was entered directly" }))] }));
}
