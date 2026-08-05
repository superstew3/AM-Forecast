import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";
export default function Movement() {
    const params = new URLSearchParams();
    const q = useQuery({ queryKey: ["movement"], queryFn: () => api.forecastMovement(params) });
    if (q.isLoading)
        return _jsx(Loading, { what: "forecast movement" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    const t = d.totals;
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Forecast movement ", _jsx("span", { className: "fy", children: "Original to Latest" })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Notes, { notes: d.meta.notes }), _jsx(Panel, { title: "Movement summary", subtitle: "A removed policy reduces Latest Forecast and is reported here. It never creates negative forecast income.", children: _jsxs("div", { className: "metric-grid", children: [_jsxs("div", { className: "metric", children: [_jsx("div", { className: "metric-label", children: "Policies removed" }), _jsx("div", { className: "metric-value", children: t.policies_removed }), _jsxs("div", { className: "metric-sub", children: [money({ value: t.income_removed, available: true }), " removed"] })] }), _jsxs("div", { className: "metric", children: [_jsx("div", { className: "metric-label", children: "Policies added" }), _jsx("div", { className: "metric-value", children: t.policies_added }), _jsxs("div", { className: "metric-sub", children: [money({ value: t.income_added, available: true }), " added"] })] }), _jsxs("div", { className: "metric", children: [_jsx("div", { className: "metric-label", children: "Amount changes" }), _jsx("div", { className: "metric-value", children: money({ value: t.amount_changes, available: true }) })] }), _jsxs("div", { className: "metric", children: [_jsxs("div", { className: "metric-label", children: ["Manager transfers", _jsx("span", { className: "hint", title: "Counted from the independent manager-change flag, so a policy that also changed amount is still counted here.", children: "i" })] }), _jsx("div", { className: "metric-value", children: t.manager_transfers })] }), _jsxs("div", { className: "metric", children: [_jsx("div", { className: "metric-label", children: "Detail changes" }), _jsx("div", { className: "metric-value", children: t.detail_changes })] }), _jsxs("div", { className: "metric", children: [_jsx("div", { className: "metric-label", children: "Several changes at once" }), _jsx("div", { className: "metric-value", children: t.multi_attribute_changes }), _jsx("div", { className: "metric-sub", children: "policies carrying more than one change" })] }), _jsxs("div", { className: "metric metric-emphasis", children: [_jsx("div", { className: "metric-label", children: "Net forecast movement" }), _jsx("div", { className: "metric-value", children: money({ value: t.net_forecast_movement, available: true }) })] })] }) }), _jsx(Panel, { title: "By month and manager", children: _jsx(DataTable, { caption: "movement", rows: d.summary, columns: [
                        { key: "forecast_month", label: "Month", render: (r) => monthAU(r.forecast_month) },
                        { key: "canonical_manager", label: "Manager" },
                        { key: "policies_removed", label: "Removed", align: "right" },
                        { key: "expected_income_removed", label: "Income removed", align: "right",
                            render: (r) => money({ value: r.expected_income_removed, available: true }) },
                        { key: "policies_added", label: "Added", align: "right" },
                        { key: "expected_income_added", label: "Income added", align: "right",
                            render: (r) => money({ value: r.expected_income_added, available: true }) },
                        { key: "amount_changes", label: "Amount change", align: "right",
                            render: (r) => money({ value: r.amount_changes, available: true }) },
                        { key: "manager_transfers", label: "Transfers", align: "right" },
                        { key: "detail_changes", label: "Detail changes", align: "right" },
                        { key: "multi_attribute_changes", label: "Multi-change", align: "right" },
                    ] }) }), _jsx(Panel, { title: "Policy detail", subtitle: `${d.detail.total} movement records. Every summary figure drills to these rows.`, actions: _jsx("a", { className: "button", href: api.exportUrl("forecast-movement", "csv", params), children: "Export CSV" }), children: _jsx(DataTable, { caption: "policy movements", rows: d.detail.items, columns: [
                        { key: "policy_id", label: "PolicyID" },
                        { key: "forecast_month", label: "Month", render: (r) => monthAU(r.forecast_month) },
                        { key: "client_code", label: "Client" },
                        { key: "policy_number", label: "Policy number" },
                        { key: "movement_type", label: "Primary change" },
                        { key: "secondary_changes", label: "All changes",
                            render: (r) => (r.secondary_changes?.length ? r.secondary_changes.join(", ") : "\u2014") },
                        { key: "previous_income", label: "Previous", align: "right",
                            render: (r) => money({ value: r.previous_income, available: true }) },
                        { key: "latest_income", label: "Latest", align: "right",
                            render: (r) => money({ value: r.latest_income, available: true }) },
                        { key: "movement_amount", label: "Movement", align: "right",
                            render: (r) => money({ value: r.movement_amount, available: true }) },
                        { key: "canonical_from_manager", label: "From" },
                        { key: "canonical_to_manager", label: "To" },
                    ] }) })] }));
}
