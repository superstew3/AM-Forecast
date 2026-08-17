import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { api, monthAU } from "../lib/api";
import { NOT_YET } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel, Value } from "../components/ui";
const PERIODS = [
    { key: "month", label: "Monthly" },
    { key: "quarter", label: "Quarterly" },
    { key: "ytd", label: "Year to date" },
    { key: "year", label: "Full year" },
];
export default function Managers() {
    const [period, setPeriod] = useState("quarter");
    const { years, currentFy, label: fyLabelOf } = usePeriods();
    const [fyPick, setFyPick] = useState(null);
    // Default to the current financial year once the data says what it is.
    const fy = fyPick ?? currentFy ?? new Date().getFullYear();
    const setFy = setFyPick;
    const [includeNonRanked, setIncludeNonRanked] = useState(false);
    const params = new URLSearchParams({
        period,
        financial_year: String(fy),
        include_non_ranked: String(includeNonRanked),
    });
    const fyLabel = fyLabelOf(fy);
    const q = useQuery({
        queryKey: ["managers", period, fy, includeNonRanked],
        queryFn: () => api.managers(params),
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "manager performance" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    /** A period that has not started shows an em dash, not N/A. */
    const val = (r, m, kind = "money") => r.has_started
        ? _jsx(Value, { m: m, kind: kind })
        : _jsx("span", { className: "not-yet", title: "This period has not started yet.", children: NOT_YET });
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
                ? [{ key: "financial_quarter", label: "Quarter",
                        render: (r) => r.financial_quarter
                            ? _jsxs(_Fragment, { children: ["Q", r.financial_quarter, " ", _jsx("span", { className: "qtr-fy", children: fyLabel })] })
                            : "\u2014" }]
                : [{ key: "financial_year", label: "Period",
                        render: () => _jsxs(_Fragment, { children: [period === "ytd" ? "Year to date" : "Full year", " ", _jsx("span", { className: "qtr-fy", children: fyLabel })] }) }]),
        { key: "original_forecast", label: "Renewal Forecast", align: "right",
            render: (r) => _jsx(Value, { m: r.original_forecast }) },
        { key: "positive_actual_income", label: "Positive Actual", align: "right",
            render: (r) => val(r, r.positive_actual_income) },
        { key: "return_income", label: "Return Income", align: "right",
            render: (r) => val(r, r.return_income) },
        { key: "net_actual_income", label: "Net Actual", align: "right",
            render: (r) => val(r, r.net_actual_income) },
        { key: "new_business_growth_target", label: "NB Target", align: "right",
            render: (r) => _jsx(Value, { m: r.new_business_growth_target }) },
        { key: "total_budget", label: "Total Budget", align: "right",
            render: (r) => _jsx(Value, { m: r.total_budget }) },
        { key: "budget_variance", label: "Variance", align: "right",
            render: (r) => val(r, r.budget_variance) },
        { key: "budget_to_date", label: "Budget to date", align: "right",
            hint: "Budget for the months elapsed. A quarter one month in is measured against one month of budget, not three.",
            render: (r) => val(r, r.budget_to_date) },
        { key: "budget_verdict", label: "Result", align: "left",
            render: (r) => !r.has_started
                ? _jsx("span", { className: "not-yet", children: NOT_YET })
                : r.budget_verdict === "Not measurable"
                    ? _jsx("span", { className: "na", title: "No budget applies for this period.", children: "Not measurable" })
                    : _jsxs("span", { className: `verdict-chip ${r.budget_verdict === "Made budget" ? "made" : "below"}`, children: [r.budget_verdict, r.over_or_under_pct?.available && (_jsxs("b", { children: [Number(r.over_or_under_pct.value) >= 0 ? "+" : "", (Number(r.over_or_under_pct.value) * 100).toFixed(1), "%"] }))] }) },
        { key: "budget_achievement", label: "Budget %", align: "right",
            render: (r) => val(r, r.budget_achievement, "percent") },
        { key: "renewal_achievement", label: "Renewal %", align: "right",
            hint: "Actual RWL/TRW income against the Original Renewal Forecast. N/A where no usable baseline exists.",
            render: (r) => val(r, r.renewal_achievement, "percent") },
        { key: "actual_new_business", label: "Actual NB", align: "right",
            render: (r) => val(r, r.actual_new_business) },
        { key: "latest_outlook", label: "Outlook", align: "right",
            render: (r) => _jsx(Value, { m: r.latest_outlook }) },
        { key: "remaining_budget_gap", label: "Gap", align: "right",
            render: (r) => _jsx(Value, { m: r.remaining_budget_gap }) },
        { key: "renewal_income", label: "Renewal income", align: "right",
            hint: "Actual RWL and TRW income for the months elapsed.",
            render: (r) => val(r, r.renewal_income) },
    ];
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Compare managers ", _jsx("span", { className: "fy", children: fyLabel })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Panel, { title: `Performance — ${PERIODS.find((p) => p.key === period).label}, ${fyLabel}`, subtitle: "Inactive, legacy and unmapped managers are out of rankings by default. Their actual income still counts towards business totals.", actions: _jsxs("div", { className: "controls", children: [_jsxs("label", { children: ["Compare by", _jsx("select", { value: period, onChange: (e) => setPeriod(e.target.value), children: PERIODS.map((p) => (_jsx("option", { value: p.key, children: p.label }, p.key))) })] }), _jsxs("label", { children: ["Financial year", _jsx("select", { value: fy, onChange: (e) => setFy(Number(e.target.value)), children: _jsx(YearOptions, { years: years }) })] }), _jsxs("label", { className: "check", children: [_jsx("input", { type: "checkbox", checked: includeNonRanked, onChange: (e) => setIncludeNonRanked(e.target.checked) }), "Show non-ranked managers"] }), _jsx("a", { className: "button", href: api.exportUrl("managers", "csv", params), children: "Export CSV" })] }), children: _jsx(DataTable, { columns: columns, rows: d.items, caption: "manager performance" }) })] }));
}
