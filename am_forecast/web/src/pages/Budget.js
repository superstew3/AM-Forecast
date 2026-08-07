import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, money, monthAU, percent } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";
export default function Budget() {
    const qc = useQueryClient();
    const [scope, setScope] = useState("manager");
    const [manager, setManager] = useState("");
    const [quarter, setQuarter] = useState("");
    const [pct, setPct] = useState("0.075");
    const [dollars, setDollars] = useState("");
    const [reason, setReason] = useState("");
    const q = useQuery({ queryKey: ["budget", 2026], queryFn: () => api.budget(2026) });
    const audit = useQuery({ queryKey: ["budget-audit"], queryFn: api.budgetAudit });
    const save = useMutation({
        mutationFn: (body) => api.post("/api/budget/growth-rate", body),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["budget"] });
            qc.invalidateQueries({ queryKey: ["budget-audit"] });
            setReason("");
        },
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "budget" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Budget ", _jsx("span", { className: "fy", children: "FY2026-27" })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Notes, { notes: d.meta.notes }), _jsxs(Panel, { title: "Adjust the growth assumption", subtitle: "Resolution is most-specific-first: manager and quarter, then manager, then global. A dollar override supersedes the percentage at its level.", children: [_jsxs("div", { className: "form-row", children: [_jsxs("label", { children: ["Scope", _jsxs("select", { value: scope, onChange: (e) => setScope(e.target.value), children: [_jsx("option", { value: "global", children: "Global \u2014 every manager" }), _jsx("option", { value: "manager", children: "Manager" }), _jsx("option", { value: "manager_quarter", children: "Manager and quarter" })] })] }), scope !== "global" && (_jsxs("label", { children: ["Manager", _jsxs("select", { value: manager, onChange: (e) => setManager(e.target.value), children: [_jsx("option", { value: "", children: "Choose\u2026" }), [...new Set(d.quarters.map((q) => q.canonical_manager))]
                                                .sort().map((m) => _jsx("option", { value: m, children: m }, m))] })] })), scope === "manager_quarter" && (_jsxs("label", { children: ["Quarter", _jsxs("select", { value: quarter, onChange: (e) => setQuarter(e.target.value), children: [_jsx("option", { value: "", children: "\u2026" }), [1, 2, 3, 4].map((n) => _jsxs("option", { value: n, children: ["Q", n] }, n))] })] })), _jsxs("label", { children: ["Growth %", _jsx("input", { value: pct, onChange: (e) => setPct(e.target.value), placeholder: "0.075" })] }), _jsxs("label", { children: ["Dollar override", _jsx("input", { value: dollars, onChange: (e) => setDollars(e.target.value), placeholder: "optional" })] }), _jsxs("label", { className: "grow", children: ["Reason", _jsx("input", { value: reason, onChange: (e) => setReason(e.target.value), placeholder: "why this assumption changed" })] }), _jsx("button", { className: "primary", disabled: reason.length < 3 || save.isPending
                                    || (scope !== "global" && !manager), onClick: () => save.mutate({
                                    scope,
                                    canonical_manager: scope === "global" ? null : manager,
                                    financial_year: scope === "manager_quarter" ? 2026 : null,
                                    financial_quarter: scope === "manager_quarter" ? Number(quarter) : null,
                                    growth_pct: dollars ? null : Number(pct),
                                    dollar_override: dollars ? Number(dollars) : null,
                                    reason,
                                }), children: "Save assumption" })] }), save.isError && _jsx(Failed, { error: save.error }), scope === "global" && (_jsxs("div", { className: "warning", children: [_jsx("strong", { children: "Global applies to every manager." }), " To change one manager, choose Manager scope, or use the control on that manager's own page."] })), _jsx("p", { className: "footnote", children: "Changing the forecast never changes the Budget. A lapse, a removal or a returned cancellation reduces actual performance and outlook, not the target." })] }), _jsx(Panel, { title: "Quarterly budget", subtitle: "The active assumption and the level of the hierarchy that supplied it are shown on every row.", children: _jsx(DataTable, { caption: "quarterly budget", rows: d.quarters, columns: [
                        { key: "canonical_manager", label: "Manager" },
                        { key: "financial_quarter", label: "Qtr", render: (r) => `Q${r.financial_quarter}` },
                        { key: "original_renewal_forecast", label: "Original Forecast", align: "right",
                            render: (r) => money({ value: r.original_renewal_forecast, available: true }) },
                        { key: "growth_basis", label: "Assumption from",
                            render: (r) => _jsx("span", { className: "chip", children: r.growth_basis }) },
                        { key: "growth_pct", label: "Growth %", align: "right",
                            render: (r) => (r.growth_pct === null ? "N/A"
                                : percent({ value: r.growth_pct, available: true })) },
                        { key: "dollar_override", label: "Dollar override", align: "right",
                            render: (r) => money({ value: r.dollar_override,
                                available: r.dollar_override !== null,
                                reason: "No dollar override; the percentage is active." }) },
                        { key: "new_business_growth_target", label: "NB target", align: "right",
                            render: (r) => money({ value: r.new_business_growth_target, available: true }) },
                        { key: "total_budget", label: "Total Budget", align: "right",
                            render: (r) => money({ value: r.total_budget, available: true }) },
                    ] }) }), _jsx(Panel, { title: "Monthly allocation", subtitle: "The quarterly target is spread by each month's share of that quarter's Original Renewal Forecast, not in equal thirds.", children: _jsx(DataTable, { caption: "monthly budget", rows: d.monthly, columns: [
                        { key: "canonical_manager", label: "Manager" },
                        { key: "forecast_month", label: "Month", render: (r) => monthAU(r.forecast_month) },
                        { key: "original_forecast", label: "Original Forecast", align: "right",
                            render: (r) => money({ value: r.original_forecast, available: true }) },
                        { key: "allocation_method", label: "Allocation" },
                        { key: "calculated_growth_target", label: "Calculated", align: "right",
                            render: (r) => money({ value: r.calculated_growth_target, available: true }) },
                        { key: "override_amount", label: "Override", align: "right",
                            render: (r) => money({ value: r.override_amount,
                                available: r.override_amount !== null,
                                reason: "No monthly override; the calculated value is active." }) },
                        { key: "new_business_growth_target", label: "Final", align: "right",
                            render: (r) => money({ value: r.new_business_growth_target, available: true }) },
                        { key: "override_reason", label: "Reason",
                            render: (r) => r.override_reason ?? "\u2014" },
                        { key: "total_budget", label: "Total Budget", align: "right",
                            render: (r) => money({ value: r.total_budget, available: true }) },
                    ] }) }), _jsx(Panel, { title: "Budget audit history", children: audit.data && (_jsx(DataTable, { caption: "budget changes", rows: audit.data.items, columns: [
                        { key: "performed_at", label: "When",
                            render: (r) => new Date(r.performed_at).toLocaleString("en-AU") },
                        { key: "performed_by", label: "User" },
                        { key: "action", label: "Action" },
                        { key: "scope_description", label: "Scope" },
                        { key: "canonical_manager", label: "Manager",
                            render: (r) => r.canonical_manager ?? "all" },
                        { key: "before_value", label: "Before",
                            render: (r) => (r.before_value ? JSON.stringify(r.before_value) : "\u2014") },
                        { key: "after_value", label: "After",
                            render: (r) => JSON.stringify(r.after_value) },
                        { key: "reason", label: "Reason" },
                    ] })) })] }));
}
