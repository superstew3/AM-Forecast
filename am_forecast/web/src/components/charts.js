import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * Charts.
 *
 * Hand-rolled SVG rather than a charting library, for two reasons: the static
 * preview has to render identically without a bundler, and every chart here is
 * simple enough that a library would be more configuration than code.
 *
 * Charts render values the API has already computed. They never aggregate.
 * A month with no data is drawn as absent, not as a zero-height bar — a bar of
 * zero says "we earned nothing", which is a different claim from "this month
 * has not happened".
 */
import { useState } from "react";
const fmtShort = (n) => {
    const abs = Math.abs(n);
    if (abs >= 1_000_000)
        return `${(n / 1_000_000).toFixed(1)}M`;
    if (abs >= 1_000)
        return `${Math.round(n / 1000)}k`;
    return n.toFixed(0);
};
/** Negatives read the same way everywhere: accounting parentheses. */
const fmtFull = (n) => {
    const text = new Intl.NumberFormat("en-AU", {
        style: "currency", currency: "AUD", maximumFractionDigits: 0,
    }).format(Math.abs(n));
    return n < 0 ? `(${text})` : text;
};
/**
 * Actual against budget by month, with prior year as a reference line.
 * Bars are omitted, not zeroed, where a month has not started.
 */
export function MonthlyBars({ data, height = 240, onSelect, selected }) {
    const [hover, setHover] = useState(null);
    const values = data.flatMap((d) => [d.actual, d.budget, d.prior].filter((v) => v != null && !Number.isNaN(v)));
    const max = Math.max(1, ...values.map(Math.abs));
    const w = 100 / data.length;
    const pad = 34;
    const plot = height - pad;
    const scale = (v) => (Math.abs(v) / max) * (plot - 12);
    return (_jsxs("div", { className: "chart", children: [_jsxs("svg", { viewBox: `0 0 100 ${height}`, preserveAspectRatio: "none", role: "img", "aria-label": "Actual against budget by month", children: [[0.25, 0.5, 0.75, 1].map((f) => (_jsx("line", { x1: "0", x2: "100", y1: plot - f * (plot - 12), y2: plot - f * (plot - 12), className: "grid-line" }, f))), data.map((d, i) => {
                        const x = i * w;
                        const isSel = selected === d.label;
                        return (_jsxs("g", { onMouseEnter: () => setHover(i), onMouseLeave: () => setHover(null), onClick: () => onSelect?.(d.label), className: `bar-group${onSelect ? " clickable" : ""}${isSel ? " selected" : ""}`, children: [_jsx("rect", { x: x, y: "0", width: w, height: plot, className: "bar-hit" }), d.budget != null && (_jsx("rect", { x: x + w * 0.18, y: plot - scale(d.budget), width: w * 0.64, height: scale(d.budget), className: "bar-budget" })), d.actual != null && (_jsx("rect", { x: x + w * 0.3, y: plot - scale(d.actual), width: w * 0.4, height: scale(d.actual), className: `bar-actual${d.actual < 0 ? " negative" : ""}` })), d.prior != null && (_jsx("line", { x1: x + w * 0.12, x2: x + w * 0.88, y1: plot - scale(d.prior), y2: plot - scale(d.prior), className: "line-prior" }))] }, d.label));
                    })] }), _jsx("div", { className: "chart-axis", children: data.map((d) => (_jsx("span", { className: d.started === false ? "future" : "", children: d.label }, d.label))) }), hover !== null && data[hover] && (_jsxs("div", { className: "chart-tip", children: [_jsx("strong", { children: data[hover].label }), data[hover].actual != null && _jsxs("span", { children: ["Actual ", fmtFull(data[hover].actual)] }), data[hover].budget != null && _jsxs("span", { children: ["Budget ", fmtFull(data[hover].budget)] }), data[hover].prior != null && _jsxs("span", { children: ["Prior yr ", fmtFull(data[hover].prior)] }), data[hover].actual == null && data[hover].started === false &&
                        _jsx("span", { className: "muted", children: "Not started" })] })), _jsxs("div", { className: "chart-legend", children: [_jsxs("span", { children: [_jsx("i", { className: "swatch bar-actual" }), "Actual"] }), _jsxs("span", { children: [_jsx("i", { className: "swatch bar-budget" }), "Budget"] }), _jsxs("span", { children: [_jsx("i", { className: "swatch line-prior" }), "Prior year"] }), _jsxs("span", { className: "chart-scale", children: ["Peak ", fmtShort(max)] })] })] }));
}
/** Horizontal composition bar: which categories make up a total. */
export function CompositionBar({ items, onSelect, selected }) {
    const total = items.reduce((s, i) => s + Math.abs(i.value), 0) || 1;
    return (_jsxs("div", { className: "composition", children: [_jsx("div", { className: "composition-bar", children: items.map((i, idx) => (_jsx("div", { className: `seg seg-${idx % 8}${selected === i.label ? " selected" : ""}${onSelect ? " clickable" : ""}`, style: { width: `${(Math.abs(i.value) / total) * 100}%` }, title: `${i.label}: ${fmtFull(i.value)} (${((Math.abs(i.value) / total) * 100).toFixed(1)}%)`, onClick: () => onSelect?.(i.label) }, i.label))) }), _jsx("ul", { className: "composition-key", children: items.map((i, idx) => (_jsxs("li", { className: `${selected === i.label ? "selected" : ""}${onSelect ? " clickable" : ""}`, onClick: () => onSelect?.(i.label), children: [_jsx("i", { className: `swatch seg-${idx % 8}` }), _jsx("span", { className: "key-label", children: i.label }), _jsx("span", { className: "key-value", children: fmtFull(i.value) }), _jsxs("span", { className: "key-share", children: [((Math.abs(i.value) / total) * 100).toFixed(1), "%"] })] }, i.label))) })] }));
}
/** Diverging bars for year-on-year movement: who is up, who is down. */
export function ChangeBars({ items, limit = 10 }) {
    const shown = [...items]
        .sort((a, b) => Math.abs(b.change) - Math.abs(a.change))
        .slice(0, limit)
        .sort((a, b) => b.change - a.change);
    const max = Math.max(1, ...shown.map((i) => Math.abs(i.change)));
    return (_jsx("ul", { className: "change-bars", children: shown.map((i) => {
            const pct = (Math.abs(i.change) / max) * 50;
            return (_jsxs("li", { title: `${i.label}: ${fmtFull(i.change)}`, children: [_jsx("span", { className: "change-label", children: i.label }), _jsxs("span", { className: "change-track", children: [_jsx("span", { className: "change-axis" }), _jsx("span", { className: `change-fill ${i.change >= 0 ? "up" : "down"}`, style: i.change >= 0
                                    ? { left: "50%", width: `${pct}%` }
                                    : { right: "50%", width: `${pct}%` } })] }), _jsxs("span", { className: `change-value ${i.change >= 0 ? "up" : "down"}`, children: [i.change >= 0 ? "+" : "", fmtFull(i.change)] })] }, i.label));
        }) }));
}
/** Progress towards budget, with an explicit over/under verdict. */
export function BudgetGauge({ actual, budget, label }) {
    if (actual == null || budget == null || budget === 0) {
        return _jsx("div", { className: "gauge na-gauge", children: "No budget applies, so achievement is N/A." });
    }
    const ratio = actual / budget;
    const pct = Math.min(Math.abs(ratio), 1.5) / 1.5 * 100;
    const over = ratio >= 1;
    return (_jsxs("div", { className: `gauge ${over ? "over" : "under"}`, children: [_jsxs("div", { className: "gauge-track", children: [_jsx("span", { className: "gauge-fill", style: { width: `${pct}%` } }), _jsx("span", { className: "gauge-target", style: { left: `${(1 / 1.5) * 100}%` } })] }), _jsxs("div", { className: "gauge-verdict", children: [_jsxs("strong", { children: [(ratio * 100).toFixed(1), "%"] }), " of ", label ?? "budget", " \u2014", " ", _jsxs("span", { className: over ? "good" : "bad", children: [over ? "over budget" : "under budget", " by ", fmtFull(Math.abs(actual - budget)), " ", "(", Math.abs((ratio - 1) * 100).toFixed(1), "%)"] })] })] }));
}
