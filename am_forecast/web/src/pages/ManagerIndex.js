import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { useState } from "react";
import { Failed, GstBanner, Loading, Panel, Value } from "../components/ui";
/**
 * Account manager index.
 *
 * The manager area used to open on whichever name sorted first, which quietly
 * implied that manager mattered more than the rest. It now opens on everyone,
 * and you choose.
 */
export default function ManagerIndex() {
    const { years, currentFy, label } = usePeriods();
    const [fyPick, setFyPick] = useState(null);
    const fy = fyPick ?? currentFy ?? new Date().getFullYear();
    const [showAll, setShowAll] = useState(false);
    const params = new URLSearchParams({
        period: "ytd", financial_year: String(fy),
        include_non_ranked: String(showAll),
    });
    const q = useQuery({
        queryKey: ["managers", "ytd", fy, showAll],
        queryFn: () => api.managers(params),
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "account managers" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const rows = q.data.items;
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Account managers", _jsxs("span", { className: "fy", children: [label(fy), " year to date"] }), _jsx("select", { className: "inline-select", value: fy, onChange: (e) => setFyPick(Number(e.target.value)), children: _jsx(YearOptions, { years: years }) })] }), _jsx(GstBanner, { meta: q.data.meta }), _jsx(Panel, { title: "Choose a manager", subtitle: "Year-to-date position for each. Open one for the full month-by-month view, budget growth and bonus.", actions: _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: showAll, onChange: (e) => setShowAll(e.target.checked) }), "Include non-ranked"] }), children: _jsx("div", { className: "manager-cards", children: rows.map((r) => {
                        const made = r.budget_verdict === "Made budget";
                        const measurable = r.budget_achievement?.available;
                        return (_jsxs(Link, { className: "manager-card", to: `/manager?name=${encodeURIComponent(r.canonical_manager)}`, children: [_jsxs("div", { className: "manager-card-head", children: [_jsx("strong", { children: r.canonical_manager }), !r.include_in_rankings && _jsx("span", { className: "chip", children: "not ranked" })] }), _jsxs("div", { className: "manager-card-figure", children: [_jsx(Value, { m: r.net_actual_income }), _jsx("span", { className: "manager-card-caption", children: "net actual, year to date" })] }), _jsxs("div", { className: "manager-card-row", children: [_jsx("span", { children: "Budget" }), _jsx(Value, { m: r.budget_to_date })] }), _jsxs("div", { className: "manager-card-row", children: [_jsx("span", { children: "Renewal" }), _jsx(Value, { m: r.renewal_achievement, kind: "percent" })] }), _jsx("div", { className: `manager-card-verdict ${!measurable ? "neutral" : made ? "good" : "bad"}`, children: measurable ? (_jsxs(_Fragment, { children: [r.budget_verdict, r.over_or_under_pct?.available && (_jsxs("b", { children: [Number(r.over_or_under_pct.value) >= 0 ? "+" : "", (Number(r.over_or_under_pct.value) * 100).toFixed(1), "%"] }))] })) : "Not measurable yet" })] }, r.canonical_manager));
                    }) }) })] }));
}
