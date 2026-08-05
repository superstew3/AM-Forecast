import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, monthAU } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel, Value } from "../components/ui";
const PERIODS = [
    { key: "month", label: "Monthly" },
    { key: "quarter", label: "Quarterly" },
    { key: "ytd", label: "Year to date" },
    { key: "year", label: "Full year" },
];
export default function Managers() {
    const [period, setPeriod] = useState("quarter");
    const [includeNonRanked, setIncludeNonRanked] = useState(false);
    const params = new URLSearchParams({
        period,
        financial_year: "2026",
        include_non_ranked: String(includeNonRanked),
    });
    const q = useQuery({
        queryKey: ["managers", period, includeNonRanked],
        queryFn: () => api.managers(params),
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "manager performance" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    const columns = [
        {
            key: "canonical_manager",
            label: "Manager",
            render: (r) => (_jsxs(_Fragment, { children: [r.canonical_manager, !r.include_in_rankings && (_jsx("span", { className: "chip", title: "Excluded from rankings by default. Actual income still counts towards business totals.", children: r.status === "legacy_unmapped" ? "legacy" : r.status }))] })),
        },
        ...(period === "month"
            ? [{ key: "period_month", label: "Month",
                    render: (r) => monthAU(r.period_month) }]
            : period === "quarter"
                ? [{ key: "financial_quarter", label: "Qtr",
                        render: (r) => (r.financial_quarter ? `Q${r.financial_quarter}` : "\u2014") }]
                : []),
        { key: "original_forecast", label: "Original Forecast", align: "right",
            render: (r) => _jsx(Value, { m: r.original_forecast }) },
        { key: "latest_forecast", label: "Latest Forecast", align: "right",
            hint: "N/A for completed months, which report actuals.",
            render: (r) => _jsx(Value, { m: r.latest_forecast }) },
        { key: "positive_actual_income", label: "Positive Actual", align: "right",
            render: (r) => _jsx(Value, { m: r.positive_actual_income }) },
        { key: "return_income", label: "Return Income", align: "right",
            render: (r) => _jsx(Value, { m: r.return_income }) },
        { key: "net_actual_income", label: "Net Actual", align: "right",
            render: (r) => _jsx(Value, { m: r.net_actual_income }) },
        { key: "new_business_growth_target", label: "NB Target", align: "right",
            render: (r) => _jsx(Value, { m: r.new_business_growth_target }) },
        { key: "total_budget", label: "Total Budget", align: "right",
            render: (r) => _jsx(Value, { m: r.total_budget }) },
        { key: "budget_variance", label: "Variance", align: "right",
            render: (r) => _jsx(Value, { m: r.budget_variance }) },
        { key: "budget_achievement", label: "Budget %", align: "right",
            render: (r) => _jsx(Value, { m: r.budget_achievement, kind: "percent" }) },
        { key: "renewal_achievement", label: "Renewal %", align: "right",
            hint: "Actual RWL/TRW income against the Original Renewal Forecast. N/A where no usable baseline exists.",
            render: (r) => _jsx(Value, { m: r.renewal_achievement, kind: "percent" }) },
        { key: "actual_new_business", label: "Actual NB", align: "right",
            render: (r) => _jsx(Value, { m: r.actual_new_business }) },
        { key: "latest_outlook", label: "Outlook", align: "right",
            render: (r) => _jsx(Value, { m: r.latest_outlook }) },
        { key: "remaining_budget_gap", label: "Gap", align: "right",
            render: (r) => _jsx(Value, { m: r.remaining_budget_gap }) },
        { key: "retention_by_income", label: "Retention (income)", align: "right",
            render: (r) => _jsx(Value, { m: r.retention_by_income, kind: "percent" }) },
    ];
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Account manager performance ", _jsx("span", { className: "fy", children: "FY2026-27" })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Panel, { title: "Performance", subtitle: "Inactive, legacy and unmapped managers are out of rankings by default. Their actual income still counts towards business totals.", actions: _jsxs("div", { className: "controls", children: [_jsx("div", { className: "segmented", children: PERIODS.map((p) => (_jsx("button", { className: period === p.key ? "on" : "", onClick: () => setPeriod(p.key), children: p.label }, p.key))) }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: includeNonRanked, onChange: (e) => setIncludeNonRanked(e.target.checked) }), "Show non-ranked managers"] }), _jsx("a", { className: "button", href: api.exportUrl("managers", "csv", params), children: "Export CSV" })] }), children: _jsx(DataTable, { columns: columns, rows: d.items, caption: "manager performance" }) })] }));
}
