import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { api, money, monthAU, percent } from "../lib/api";
import { Failed, GstBanner, Loading, Panel } from "../components/ui";
const MEASURES = [
    { key: "net_actual", label: "Net Actual", kind: "money" },
    { key: "budget", label: "Total Budget", kind: "money" },
    { key: "variance", label: "Variance to Budget", kind: "money" },
    { key: "achievement", label: "Budget Achievement", kind: "percent" },
    { key: "original_forecast", label: "Renewal Forecast", kind: "money" },
];
export default function AllManagers() {
    const { years, currentFy, label: fyLabelOf } = usePeriods();
    const [fyPick, setFyPick] = useState(null);
    // Default to the current financial year once the data says what it is.
    const fy = fyPick ?? currentFy ?? new Date().getFullYear();
    const setFy = setFyPick;
    const [measure, setMeasure] = useState("net_actual");
    const [showAll, setShowAll] = useState(false);
    const q = useQuery({
        queryKey: ["matrix", fy, measure, showAll],
        queryFn: () => api.managerMatrix(fy, measure, showAll),
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "the manager matrix" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    const kind = MEASURES.find((m) => m.key === measure).kind;
    const fmt = (v, status) => {
        if (status === "future") {
            return _jsx("span", { className: "not-yet", title: "This month has not started yet.", children: "\u2014" });
        }
        if (v === null) {
            return _jsx("span", { className: "na", title: "Not available for this period.", children: "N/A" });
        }
        return kind === "percent"
            ? percent({ value: v, available: true })
            : money({ value: v, available: true });
    };
    const cellTone = (v, status) => {
        if (status !== "actual" || v === null)
            return "";
        if (measure === "variance")
            return Number(v) >= 0 ? " cell-good" : " cell-bad";
        if (measure === "achievement")
            return Number(v) >= 1 ? " cell-good" : " cell-bad";
        return "";
    };
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["All managers by month ", _jsx("span", { className: "fy", children: fyLabelOf(fy) })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Panel, { title: MEASURES.find((m) => m.key === measure).label, subtitle: "Every manager down the side, every month across the top. One measure at a time, so no figure can be mistaken for another.", actions: _jsxs("div", { className: "controls", children: [_jsxs("label", { children: ["Measure", _jsx("select", { value: measure, onChange: (e) => setMeasure(e.target.value), children: MEASURES.map((m) => _jsx("option", { value: m.key, children: m.label }, m.key)) })] }), _jsxs("label", { children: ["Financial year", _jsx("select", { value: fy, onChange: (e) => setFy(Number(e.target.value)), children: _jsx(YearOptions, { years: years }) })] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: showAll, onChange: (e) => setShowAll(e.target.checked) }), "Include non-ranked"] })] }), children: _jsx("div", { className: "table-wrap", children: _jsxs("table", { className: "grid", children: [_jsx("thead", { children: _jsxs("tr", { children: [_jsx("th", { className: "sticky-col", children: "Account manager" }), d.months.map((m, i) => (_jsx("th", { className: `right${d.month_status[i] === "future" ? " future-col" : ""}`, children: monthAU(m) }, m))), kind !== "percent" && _jsx("th", { className: "right total-col", children: "Total" })] }) }), _jsx("tbody", { children: d.rows.map((r) => (_jsxs("tr", { children: [_jsxs("td", { className: "sticky-col", children: [r.canonical_manager, !r.include_in_rankings && _jsx("span", { className: "chip", children: "not ranked" })] }), r.cells.map((c, i) => (_jsx("td", { className: `right${d.month_status[i] === "future" ? " future-col" : ""}${cellTone(c.value, c.status)}`, children: fmt(c.value, c.status) }, c.month))), kind !== "percent" && (_jsx("td", { className: "right total-col", children: r.total === null ? "—" : money({ value: r.total, available: true }) }))] }, r.canonical_manager))) }), kind !== "percent" && (_jsx("tfoot", { children: _jsxs("tr", { children: [_jsx("td", { className: "sticky-col", children: "All managers" }), d.column_totals.map((c, i) => (_jsx("td", { className: `right${d.month_status[i] === "future" ? " future-col" : ""}`, children: fmt(c.value, c.status) }, c.month))), _jsx("td", { className: "right total-col", children: d.grand_total === null ? "—" : money({ value: d.grand_total, available: true }) })] }) }))] }) }) })] }));
}
