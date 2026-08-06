import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, percent } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { BudgetGauge, ChangeBars, MonthlyBars } from "../components/charts";
import { DataTable, Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";
const STATUS_LABEL = {
    earned: "Earned", missed: "Missed", "on track": "On track",
    behind: "Behind", "not started": "Not started",
};
/**
 * Bonus Tracker.
 *
 * Two figures are kept apart throughout: what has been earned, and what is
 * projected at the current pace. Only the first is money.
 */
export default function Bonus() {
    const { years, currentFy } = usePeriods();
    const [fyPick, setFyPick] = useState(null);
    const fy = fyPick ?? currentFy ?? new Date().getFullYear();
    const [manager, setManager] = useState(null);
    const all = useQuery({ queryKey: ["bonus", fy], queryFn: () => api.bonus(fy) });
    const one = useQuery({
        queryKey: ["bonus-manager", manager, fy],
        queryFn: () => api.bonusForManager(manager, fy),
        enabled: !!manager,
    });
    if (all.isLoading)
        return _jsx(Loading, { what: "the bonus tracker" });
    if (all.isError)
        return _jsx(Failed, { error: all.error, retry: () => all.refetch() });
    const d = all.data;
    const num = (v) => (v === null || v === undefined ? null : Number(v));
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Bonus tracker", _jsx("span", { className: "fy", children: d.financial_year_label }), _jsx("select", { className: "inline-select", value: fy, onChange: (e) => setFyPick(Number(e.target.value)), children: _jsx(YearOptions, { years: years }) })] }), _jsx(GstBanner, { meta: d.meta }), _jsxs("div", { className: "purpose", children: [_jsx("strong", { children: "How the bonus works." }), " ", d.scheme.description, _jsx("ul", { className: "formula", children: d.scheme.formula.map((f) => _jsx("li", { children: _jsx("code", { children: f }) }, f)) }), _jsx("em", { children: "Earned is not projected." }), " A quarter still running shows the bonus that would pay if it closed today \u2014 usually nil part-way through \u2014 with the projection at current pace reported separately. Projections are not money."] }), _jsx(Panel, { title: "Position across the business", children: _jsxs("div", { className: "metric-grid", children: [_jsx(Metric, { label: "Earned to date", m: { value: d.totals.earned_bonus, available: true }, emphasis: true, hint: d.column_scope.earned_bonus }), _jsx(Metric, { label: "Projected \u2014 quarters under way", m: { value: d.totals.projected_bonus, available: true }, hint: d.column_scope.projected_bonus }), _jsx(Metric, { label: "Full year at target", m: { value: d.totals.bonus_at_target, available: true }, hint: d.column_scope.bonus_at_target }), _jsx(Metric, { label: "Full year outlook", m: { value: d.totals.full_year_outlook, available: true }, hint: d.column_scope.full_year_outlook }), _jsx(Metric, { label: "Total actual income", m: { value: d.totals.actual_income, available: true } }), _jsx(Metric, { label: "Total budget target", m: { value: d.totals.budget_target, available: true } }), _jsx(Metric, { label: "Managers in scheme", m: { value: d.totals.managers, available: true }, kind: "money" })] }) }), _jsxs(Panel, { title: "Bonus by manager", subtitle: "The three bonus columns cover different periods and are not comparable with each other. Hover any heading for its exact scope. Click a manager for their quarter-by-quarter position.", children: [_jsxs("div", { className: "scope-key", children: [_jsxs("span", { children: [_jsx("strong", { children: "Earned to date" }), " \u2014 ", d.column_scope.earned_bonus] }), _jsxs("span", { children: [_jsx("strong", { children: "Projected" }), " \u2014 ", d.column_scope.projected_bonus] }), _jsxs("span", { children: [_jsx("strong", { children: "Full year at target" }), " \u2014 ", d.column_scope.bonus_at_target] })] }), _jsx(DataTable, { caption: "managers", rows: d.managers, onRowClick: (r) => setManager(r.canonical_manager), columns: [
                            { key: "canonical_manager", label: "Manager" },
                            { key: "ytd_actual", label: "Actual (started quarters)", align: "right",
                                render: (r) => money({ value: r.ytd_actual, available: true }) },
                            { key: "ytd_budget_target", label: "Target", align: "right",
                                render: (r) => money({ value: r.ytd_budget_target, available: true }) },
                            { key: "gap", label: "Above / (below)", align: "right",
                                render: (r) => money({
                                    value: Number(r.ytd_actual) - Number(r.ytd_budget_target),
                                    available: true
                                }) },
                            { key: "quarters_started", label: "Quarters started", align: "right",
                                hint: "How many of the year's four quarters have begun. The Earned and Projected columns cover only these.",
                                render: (r) => `${r.quarters_started} of ${r.quarters_total}` },
                            { key: "earned_bonus", label: "Earned to date", align: "right",
                                hint: d.column_scope.earned_bonus,
                                render: (r) => money({ value: r.earned_bonus, available: true }) },
                            { key: "projected_bonus", label: "Projected (started quarters)",
                                align: "right", hint: d.column_scope.projected_bonus,
                                render: (r) => money({ value: r.projected_bonus, available: true }) },
                            { key: "bonus_at_target", label: "Full year at target", align: "right",
                                hint: d.column_scope.bonus_at_target,
                                render: (r) => money({ value: r.bonus_at_target, available: true }) },
                            { key: "full_year_outlook", label: "Full year outlook", align: "right",
                                hint: d.column_scope.full_year_outlook,
                                render: (r) => money({ value: r.full_year_outlook, available: true }) },
                        ] })] }), _jsxs("div", { className: "two-col", children: [_jsx(Panel, { title: "Projected bonus, quarters under way", subtitle: "At the pace of the months completed. Quarters not yet begun are excluded.", children: _jsx(ChangeBars, { items: d.managers.map((m) => ({
                                label: m.canonical_manager, change: Number(m.projected_bonus),
                            })), limit: 15 }) }), _jsx(Panel, { title: "Distance to target", subtitle: "Actual income less budget target, for quarters that have started.", children: _jsx(ChangeBars, { items: d.managers.map((m) => ({
                                label: m.canonical_manager,
                                change: Number(m.ytd_actual) - Number(m.ytd_budget_target),
                            })), limit: 15 }) })] }), _jsx(Panel, { title: "Every quarter", subtitle: "A quarter that has not started carries no bonus figure at all, rather than a nil.", children: _jsx(DataTable, { caption: "quarters", rows: d.quarters, columns: [
                        { key: "canonical_manager", label: "Manager" },
                        { key: "quarter_label", label: "Quarter" },
                        { key: "months_elapsed", label: "Months",
                            render: (r) => `${r.months_elapsed}/${r.months_in_quarter}` },
                        { key: "expected_income", label: "Expected income", align: "right",
                            render: (r) => money({ value: r.expected_income, available: true }) },
                        { key: "growth_pct", label: "Growth %", align: "right",
                            render: (r) => percent({ value: r.growth_pct,
                                available: r.growth_pct !== null }) },
                        { key: "growth_target_amount", label: "Growth target", align: "right",
                            render: (r) => money({ value: r.growth_target_amount, available: true }) },
                        { key: "budget_target", label: "Budget target", align: "right",
                            render: (r) => money({ value: r.budget_target, available: true }) },
                        { key: "actual_income", label: "Actual", align: "right",
                            render: (r) => money({ value: r.actual_income,
                                available: r.quarter_started }) },
                        { key: "above_below_target", label: "Above / (below)", align: "right",
                            render: (r) => money({ value: r.above_below_target,
                                available: r.quarter_started }) },
                        { key: "income_still_required", label: "Still needed", align: "right",
                            hint: "Income still to earn before any bonus is payable.",
                            render: (r) => money({ value: r.income_still_required,
                                available: r.quarter_started }) },
                        { key: "total_bonus", label: "Bonus earned", align: "right",
                            render: (r) => money({ value: r.total_bonus,
                                available: r.total_bonus !== null }) },
                        { key: "projected_bonus", label: "Projected", align: "right",
                            render: (r) => money({ value: r.projected_bonus,
                                available: r.projected_bonus !== null }) },
                        { key: "status", label: "Status",
                            render: (r) => (_jsx("span", { className: `verdict-chip status-${r.status.replace(" ", "-")}`, children: STATUS_LABEL[r.status] ?? r.status })) },
                    ] }) }), manager && one.data && (_jsxs(Panel, { title: `${manager} — quarter by quarter`, actions: _jsx("button", { onClick: () => setManager(null), children: "Close" }), children: [one.data.quarters.map((q) => (_jsxs("div", { className: "bonus-quarter", children: [_jsxs("div", { className: "bonus-quarter-head", children: [_jsx("strong", { children: q.quarter_label }), _jsx("span", { className: `verdict-chip status-${q.status.replace(" ", "-")}`, children: STATUS_LABEL[q.status] ?? q.status }), _jsxs("span", { className: "bonus-months", children: [q.months_elapsed, " of ", q.months_in_quarter, " months complete"] })] }), _jsx(BudgetGauge, { actual: q.quarter_started ? num(q.actual_income) : null, budget: num(q.budget_target), label: `${q.quarter_label} target` }), _jsxs("div", { className: "bonus-breakdown", children: [_jsxs("span", { children: ["Expected ", money({ value: q.expected_income, available: true })] }), _jsxs("span", { children: ["+ growth ", percent({ value: q.growth_pct,
                                                available: q.growth_pct !== null })] }), _jsxs("span", { children: ["= target ", money({ value: q.budget_target, available: true })] }), _jsx("span", { className: "sep", children: "|" }), _jsxs("span", { children: ["Base ", money({ value: q.bonus_at_target, available: true })] }), _jsxs("span", { children: ["Above-target ", money({ value: q.above_target_bonus,
                                                available: q.above_target_bonus !== null })] }), _jsxs("strong", { children: ["Bonus ", money({ value: q.total_bonus,
                                                available: q.total_bonus !== null })] })] })] }, q.financial_quarter))), _jsx(MonthlyBars, { data: one.data.months.map((m) => ({
                            label: new Date(`${m.period_month}T00:00:00`).toLocaleDateString("en-AU", { month: "short", year: "2-digit" }),
                            actual: m.month_started && m.actual_income !== null
                                ? Number(m.actual_income) : null,
                            budget: m.budget_target !== null ? Number(m.budget_target) : null,
                            started: m.month_started,
                        })) }), _jsx(Notes, { notes: one.data.meta.notes })] })), _jsx(Notes, { notes: d.meta.notes })] }));
}
