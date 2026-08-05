import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { GST_NOTE, NA, isUnavailable, money, percent, reasonFor, tone, } from "../lib/api";
/** A value that may be unavailable. Renders N/A with the reason as a tooltip. */
export function Value({ m, kind = "money", digits }) {
    const unavailable = isUnavailable(m);
    const text = kind === "percent" ? percent(m, digits) :
        kind === "count" ? (unavailable ? NA : String(m.value)) :
            money(m);
    return (_jsxs("span", { className: unavailable ? "na" : "val", title: unavailable ? (reasonFor(m) ?? "Not available") : undefined, "data-available": !unavailable, children: [text, unavailable && _jsx("span", { className: "na-mark", "aria-hidden": "true", children: "?" })] }));
}
export function Metric({ label, m, kind = "money", hint, emphasis, ratio }) {
    const t = ratio ? tone(ratio) : "none";
    return (_jsxs("div", { className: `metric${emphasis ? " metric-emphasis" : ""} tone-${t}`, children: [_jsxs("div", { className: "metric-label", children: [label, hint && _jsx("span", { className: "hint", title: hint, children: "i" })] }), _jsx("div", { className: "metric-value", children: _jsx(Value, { m: m, kind: kind }) }), ratio && (_jsxs("div", { className: "metric-sub", children: ["Achievement ", _jsx(Value, { m: ratio, kind: "percent" })] }))] }));
}
export function GstBanner({ meta }) {
    return (_jsxs("div", { className: "gst-banner", children: [_jsx("strong", { children: GST_NOTE }), meta && (_jsxs("span", { className: "gst-meta", children: ["Reporting cut-off ", meta.cut_off_date, " \u00B7 ", meta.timezone] }))] }));
}
export function Notes({ notes }) {
    if (!notes?.length)
        return null;
    return (_jsx("ul", { className: "notes", children: notes.map((n) => _jsx("li", { children: n }, n)) }));
}
export function Panel({ title, subtitle, actions, children }) {
    return (_jsxs("section", { className: "panel", children: [_jsxs("header", { className: "panel-head", children: [_jsxs("div", { children: [_jsx("h2", { children: title }), subtitle && _jsx("p", { className: "subtitle", children: subtitle })] }), actions && _jsx("div", { className: "panel-actions", children: actions })] }), children] }));
}
export function Loading({ what }) {
    return _jsxs("div", { className: "state loading", children: ["Loading ", what, "\u2026"] });
}
export function Empty({ what }) {
    return _jsxs("div", { className: "state empty", children: ["No ", what, " for the current filters."] });
}
export function Failed({ error, retry }) {
    return (_jsxs("div", { className: "state error", role: "alert", children: [_jsx("strong", { children: "Could not load this view." }), _jsx("div", { children: error instanceof Error ? error.message : String(error) }), retry && _jsx("button", { onClick: retry, children: "Try again" })] }));
}
/**
 * A table whose totals come from the server.
 *
 * `total` is passed in from the API response, never computed by summing the
 * rows on screen. Summing the visible page would silently understate every
 * total the moment pagination kicks in.
 */
export function DataTable({ columns, rows, serverTotals, caption, onRowClick }) {
    if (!rows.length)
        return _jsx(Empty, { what: caption ?? "records" });
    return (_jsx("div", { className: "table-wrap", children: _jsxs("table", { children: [caption && _jsx("caption", { children: caption }), _jsx("thead", { children: _jsx("tr", { children: columns.map((c) => (_jsxs("th", { className: c.align === "right" ? "right" : "", children: [c.label, c.hint && _jsx("span", { className: "hint", title: c.hint, children: "i" })] }, c.key))) }) }), _jsx("tbody", { children: rows.map((row, i) => (_jsx("tr", { onClick: onRowClick ? () => onRowClick(row) : undefined, className: onRowClick ? "clickable" : "", children: columns.map((c) => (_jsx("td", { className: c.align === "right" ? "right" : "", children: c.render ? c.render(row) : String(row[c.key] ?? NA) }, c.key))) }, row.id ?? row.policy_id ?? row.canonical_manager ?? i))) }), serverTotals && (_jsx("tfoot", { children: _jsx("tr", { children: columns.map((c, i) => (_jsx("td", { className: c.align === "right" ? "right" : "", children: i === 0 ? "Total (all rows, from server)" : serverTotals[c.key] ?? "" }, c.key))) }) }))] }) }));
}
export function BaselineWarning({ month, source, exceptions }) {
    return (_jsxs("div", { className: "warning", role: "note", children: [_jsxs("strong", { children: [month, " uses a mixed baseline."] }), " ", "Original Forecast comes from the ", source ?? "Legacy Dashboard Forecast", " at manager-month level, not policy level. Actuals come from Sales Transactions. The two residual pending policies belong to Latest Forecast only. Policy-level renewal achievement is reliable from August 2026 onward.", exceptions?.length ? (_jsxs(_Fragment, { children: [" No baseline is available for ", exceptions.join(", "), ", which show N/A."] })) : null] }));
}
