import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, dateAU, money, monthAU, percent } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";
const OUTCOMES = ["", "renewed", "transfer_renewed", "lapsed_lost", "pending",
    "removed_from_latest", "multiple_candidates", "unmatched",
    "manually_resolved"];
export default function Policies() {
    const [outcome, setOutcome] = useState("");
    const [client, setClient] = useState("");
    const [page, setPage] = useState(0);
    const limit = 50;
    const params = new URLSearchParams({ limit: String(limit), offset: String(page * limit) });
    if (outcome)
        params.set("outcome", outcome);
    if (client)
        params.set("client", client);
    const q = useQuery({
        queryKey: ["policies", outcome, client, page],
        queryFn: () => api.policies(params),
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "policy renewals" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    return (_jsxs(_Fragment, { children: [_jsx("h1", { children: "Policy-level renewals" }), _jsx(GstBanner, { meta: d.meta }), _jsx(Notes, { notes: d.meta.notes }), _jsxs(Panel, { title: `${d.total.toLocaleString()} forecast policies`, subtitle: "Renewal income is RWL and TRW only. Total associated income includes every line attached to the policy and answers a different question.", actions: _jsxs("div", { className: "controls", children: [_jsxs("label", { children: ["Outcome", _jsx("select", { value: outcome, onChange: (e) => { setOutcome(e.target.value); setPage(0); }, children: OUTCOMES.map((o) => _jsx("option", { value: o, children: o || "All" }, o)) })] }), _jsxs("label", { children: ["Client", _jsx("input", { value: client, placeholder: "code", onChange: (e) => { setClient(e.target.value); setPage(0); } })] }), _jsx("a", { className: "button", href: api.exportUrl("policies", "csv", params), children: "Export CSV" }), _jsx("a", { className: "button", href: api.exportUrl("policies", "xlsx", params), children: "Export XLSX" })] }), children: [_jsx(DataTable, { caption: "policies", rows: d.items, columns: [
                            { key: "policy_id", label: "PolicyID" },
                            { key: "client_code", label: "Client" },
                            { key: "policy_number", label: "Policy number" },
                            { key: "class_abbrev", label: "Class" },
                            { key: "underwriter_abbrev", label: "Underwriter" },
                            { key: "expiry_date", label: "Expiry", render: (r) => dateAU(r.expiry_date) },
                            { key: "forecast_month", label: "Month", render: (r) => monthAU(r.forecast_month) },
                            { key: "original_manager", label: "Source manager" },
                            { key: "canonical_manager", label: "Canonical manager" },
                            { key: "original_forecast_income", label: "Original", align: "right",
                                render: (r) => money({ value: r.original_forecast_income, available: true }) },
                            { key: "latest_forecast_income", label: "Latest", align: "right",
                                render: (r) => money({ value: r.latest_forecast_income,
                                    available: r.latest_forecast_income !== null,
                                    reason: "Completed month: reports actuals, no Latest Forecast." }) },
                            { key: "forecast_movement", label: "Movement", align: "right",
                                render: (r) => money({ value: r.forecast_movement,
                                    available: r.forecast_movement !== null }) },
                            { key: "renewal_transaction_income", label: "Renewal income", align: "right",
                                render: (r) => money({ value: r.renewal_transaction_income, available: true }) },
                            { key: "total_associated_income", label: "Total associated", align: "right",
                                render: (r) => money({ value: r.total_associated_income, available: true }) },
                            { key: "outcome", label: "Outcome",
                                render: (r) => _jsx("span", { className: `chip outcome-${r.outcome}`, children: r.outcome }) },
                            { key: "best_tier", label: "Tier", align: "right",
                                render: (r) => (r.best_tier ?? "N/A") },
                            { key: "confidence", label: "Confidence", align: "right",
                                render: (r) => (r.confidence === null ? "N/A"
                                    : percent({ value: r.confidence, available: true }, 0)) },
                            { key: "requires_review", label: "Review",
                                render: (r) => (r.requires_review ? "Yes" : "\u2014") },
                            { key: "exception_flags", label: "Exceptions",
                                render: (r) => (r.exception_flags?.length ? r.exception_flags.join(", ") : "\u2014") },
                        ] }), _jsxs("div", { className: "pager", children: [_jsx("button", { disabled: page === 0, onClick: () => setPage((p) => p - 1), children: "Previous" }), _jsxs("span", { children: ["Rows ", page * limit + 1, "\\u2013", Math.min((page + 1) * limit, d.total), " of ", d.total.toLocaleString()] }), _jsx("button", { disabled: (page + 1) * limit >= d.total, onClick: () => setPage((p) => p + 1), children: "Next" })] })] })] }));
}
