import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";
/**
 * Forecast history.
 *
 * One timeline per manager. Each accepted Renewals Pending file adds a row,
 * stamped with when it arrived and who loaded it, so "what were we expecting
 * for March, and when did that change?" has a direct answer.
 */
export default function ForecastHistory() {
    const { years, currentFy } = usePeriods();
    const [manager, setManager] = useState("Sam Stewart");
    const [fyPick, setFyPick] = useState(null);
    const fy = fyPick ?? currentFy ?? new Date().getFullYear();
    const ref = useQuery({ queryKey: ["reference"], queryFn: api.reference });
    const q = useQuery({
        queryKey: ["forecast-history", manager, fy],
        queryFn: () => api.forecastHistory(manager, fy),
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "forecast history" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Forecast history", _jsxs("span", { className: "fy", children: [d.canonical_manager, " \u00B7 ", d.financial_year_label] })] }), _jsx(GstBanner, { meta: d.meta }), _jsxs("div", { className: "purpose", children: [_jsx("strong", { children: "What this page is for." }), " A record of what was forecast for each month, and when. Every accepted Renewals Pending file adds a row, time stamped and attributed. Read down a column to see how the expectation for that month changed; read across a row to see one forecast as it stood on the day it arrived."] }), _jsxs(Panel, { title: `${d.entry_count} forecast${d.entry_count === 1 ? "" : "s"} recorded`, subtitle: "Oldest first. The most recent Renewals Pending file is marked current.", actions: _jsxs("div", { className: "controls", children: [_jsxs("label", { children: ["Account manager", _jsx("select", { value: manager, onChange: (e) => setManager(e.target.value), children: (ref.data?.managers ?? []).map((m) => (_jsx("option", { value: m.canonical_manager, children: m.canonical_manager }, m.canonical_manager))) })] }), _jsxs("label", { children: ["Financial year", _jsx("select", { value: fy, onChange: (e) => setFyPick(Number(e.target.value)), children: _jsx(YearOptions, { years: years }) })] })] }), children: [_jsx("div", { className: "table-wrap", children: _jsxs("table", { className: "grid", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { className: "sticky-col", children: "Forecast recorded" }), d.months.map((m) => (_jsx("th", { className: "right", children: monthAU(m) }, m))), _jsx("th", { className: "right total-col", children: "Total" })] }) }), _jsx("tbody", { children: d.entries.map((e) => (_jsxs("tr", { className: e.is_current ? "history-current" : "history-row", children: [_jsxs("td", { className: "sticky-col", children: [_jsxs("div", { className: "history-label", children: [_jsx("strong", { children: e.label }), e.is_current && _jsx("span", { className: "chip current", children: "current" }), e.kind !== "snapshot" && _jsx("span", { className: "chip", children: "baseline" })] }), _jsxs("div", { className: "history-meta", children: [e.recorded_at
                                                                ? new Date(e.recorded_at).toLocaleString("en-AU", {
                                                                    day: "2-digit", month: "short", year: "numeric",
                                                                    hour: "2-digit", minute: "2-digit",
                                                                    timeZone: "Australia/Melbourne",
                                                                })
                                                                : "—", e.recorded_by && _jsxs(_Fragment, { children: [" \u00B7 ", e.recorded_by] }), e.source_file && _jsxs(_Fragment, { children: [" \u00B7 ", _jsx("code", { children: e.source_file })] })] })] }), e.cells.map((c) => (_jsx("td", { className: "right", children: c.value === null ? (_jsx("span", { className: "not-yet", title: "This forecast did not cover this month.", children: "\u2014" })) : (_jsxs(_Fragment, { children: [_jsx("span", { className: Number(c.value) < 0 ? "val negative" : "val", children: money({ value: c.value, available: true }) }), c.change !== null && (_jsxs("span", { className: `delta ${Number(c.change) >= 0 ? "up" : "down"}`, title: "Change from the previous forecast", children: [Number(c.change) >= 0 ? "▲" : "▼", " ", money({ value: Math.abs(Number(c.change)),
                                                                    available: true })] })), c.is_new && _jsx("span", { className: "chip new", children: "new" })] })) }, c.month))), _jsxs("td", { className: "right total-col", children: [_jsx("span", { className: Number(e.total) < 0 ? "val negative" : "val", children: e.total === null ? "—" : money({ value: e.total, available: true }) }), e.total_change !== null && e.total_change !== undefined && (_jsxs("span", { className: `delta ${Number(e.total_change) >= 0 ? "up" : "down"}`, children: [Number(e.total_change) >= 0 ? "▲" : "▼", " ", money({ value: Math.abs(Number(e.total_change)),
                                                                available: true })] }))] })] }, e.entry_id))) })] }) }), _jsx(Notes, { notes: d.meta.notes })] })] }));
}
