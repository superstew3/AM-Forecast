import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, dateAU, money } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel } from "../components/ui";
const TABS = [
    { key: "actionable", label: "Needs a decision" },
    { key: "timing", label: "July timing artefacts" },
    { key: "out_of_scope", label: "Outside matching scope" },
];
export default function Review() {
    const [kind, setKind] = useState("actionable");
    const [reason, setReason] = useState("");
    const qc = useQueryClient();
    const params = new URLSearchParams({ limit: "100" });
    const q = useQuery({ queryKey: ["review", kind], queryFn: () => api.review(kind, params) });
    const history = useQuery({ queryKey: ["review-history"], queryFn: api.reviewHistory });
    const decide = useMutation({
        mutationFn: (body) => api.post(body.path, body.payload),
        onSuccess: () => {
            qc.invalidateQueries({ queryKey: ["review"] });
            qc.invalidateQueries({ queryKey: ["review-history"] });
            setReason("");
        },
    });
    if (q.isLoading)
        return _jsx(Loading, { what: "the review queue" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    const c = d.counts;
    return (_jsxs(_Fragment, { children: [_jsx("h1", { children: "Matching review queue" }), _jsx(GstBanner, { meta: d.meta }), _jsx(Panel, { title: "Queue composition", subtitle: "Only the first group needs individual decisions. The other two are bulk artefacts with a known cause and are separated so they cannot bury the real exceptions.", children: _jsxs("div", { className: "metric-grid", children: [_jsxs("div", { className: "metric metric-emphasis", children: [_jsx("div", { className: "metric-label", children: "Needs a decision" }), _jsx("div", { className: "metric-value", children: c.actionable }), _jsx("div", { className: "metric-sub", children: d.explanations.actionable })] }), _jsxs("div", { className: "metric", children: [_jsx("div", { className: "metric-label", children: "July timing artefacts" }), _jsx("div", { className: "metric-value", children: c.july_timing_artefacts }), _jsx("div", { className: "metric-sub", children: d.explanations.july_timing_artefacts })] }), _jsxs("div", { className: "metric", children: [_jsx("div", { className: "metric-label", children: "Outside matching scope" }), _jsx("div", { className: "metric-value", children: c.out_of_scope }), _jsx("div", { className: "metric-sub", children: d.explanations.out_of_scope })] })] }) }), _jsxs(Panel, { title: "Candidates", actions: _jsx("div", { className: "segmented", children: TABS.map((t) => (_jsx("button", { className: kind === t.key ? "on" : "", onClick: () => setKind(t.key), children: t.label }, t.key))) }), children: [kind !== "actionable" && (_jsxs("div", { className: "warning", children: ["These are explained in bulk and are not individual errors.", kind === "timing"
                                ? " The Renewals Pending file was extracted after most July renewals had transacted, so there is no forecast policy to match them against."
                                : " These renewals fall in months with no policy-grain forecast, chiefly FY2025-26. There was never a forecast to match them to."] })), _jsxs("label", { className: "reason", children: ["Reason (required for any decision)", _jsx("input", { value: reason, onChange: (e) => setReason(e.target.value), placeholder: "e.g. verified against the underwriter schedule" })] }), _jsx(DataTable, { caption: "review candidates", rows: d.items, columns: [
                            { key: "reason", label: "Why" },
                            { key: "transaction_id", label: "Txn" },
                            { key: "txn_client", label: "Txn client" },
                            { key: "txn_policy_number", label: "Txn policy" },
                            { key: "txn_category", label: "Cat" },
                            { key: "transaction_date", label: "Date",
                                render: (r) => dateAU(r.transaction_date?.slice(0, 10)) },
                            { key: "txn_income", label: "Income", align: "right",
                                render: (r) => money({ value: r.txn_income, available: r.txn_income !== null }) },
                            { key: "policy_id", label: "PolicyID" },
                            { key: "policy_class", label: "Policy class" },
                            { key: "tier", label: "Tier", align: "right" },
                            { key: "actions", label: "Decision",
                                render: (r) => (kind === "actionable" && r.policy_id ? (_jsxs("span", { className: "row-actions", children: [_jsx("button", { disabled: reason.length < 3 || decide.isPending, onClick: () => decide.mutate({ path: "/api/review/match",
                                                payload: { policy_id: r.policy_id, forecast_month: r.forecast_month,
                                                    transaction_id: r.transaction_id, reason } }), children: "Match" }), _jsx("button", { disabled: reason.length < 3 || decide.isPending, onClick: () => decide.mutate({ path: "/api/review/reject",
                                                payload: { transaction_id: r.transaction_id,
                                                    policy_id: r.policy_id, reason } }), children: "Reject" })] })) : "\u2014") },
                        ] }), decide.isError && _jsx(Failed, { error: decide.error })] }), _jsx(Panel, { title: "Decision history", subtitle: "Every manual decision keeps its reviewer, timestamp, reason, previous decision and new decision.", children: history.data && (_jsx(DataTable, { caption: "decisions", rows: history.data.items, columns: [
                        { key: "decided_at", label: "When",
                            render: (r) => new Date(r.decided_at).toLocaleString("en-AU") },
                        { key: "reviewer", label: "Reviewer" },
                        { key: "action", label: "Action" },
                        { key: "policy_id", label: "PolicyID" },
                        { key: "transaction_id", label: "Txn" },
                        { key: "reason", label: "Reason" },
                        { key: "previous_decision", label: "Replaced",
                            render: (r) => (r.previous_decision ? "Yes" : "\u2014") },
                    ] })) })] }));
}
