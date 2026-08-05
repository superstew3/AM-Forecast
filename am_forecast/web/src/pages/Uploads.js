import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, money } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel } from "../components/ui";
export default function Uploads() {
    const qc = useQueryClient();
    const fileRef = useRef(null);
    const [preview, setPreview] = useState(null);
    const [reason, setReason] = useState("");
    const q = useQuery({ queryKey: ["uploads"], queryFn: api.uploads });
    const stage = useMutation({
        mutationFn: async (file) => {
            const form = new FormData();
            form.append("file", file);
            const res = await fetch("/api/uploads/prepare", {
                method: "POST", body: form,
                headers: { "X-User": "sam", "X-Role": "administrator" },
            });
            if (!res.ok)
                throw new Error((await res.json()).detail ?? "Upload failed");
            return res.json();
        },
        onSuccess: (data) => { setPreview(data); qc.invalidateQueries({ queryKey: ["uploads"] }); },
    });
    const decide = useMutation({
        mutationFn: ({ path, payload }) => api.post(path, payload),
        onSuccess: () => { setPreview(null); qc.invalidateQueries({ queryKey: ["uploads"] }); },
    });
    return (_jsxs(_Fragment, { children: [_jsx("h1", { children: "Uploads and audit history" }), _jsx(GstBanner, {}), _jsxs(Panel, { title: "Upload a report", subtitle: "Prepare stages and previews the file without touching any reported figure. The numbers below are exactly what will land on accept.", children: [_jsxs("div", { className: "form-row", children: [_jsx("input", { type: "file", ref: fileRef, accept: ".csv,.xlsx" }), _jsx("button", { disabled: stage.isPending, onClick: () => {
                                    const f = fileRef.current?.files?.[0];
                                    if (f)
                                        stage.mutate(f);
                                }, children: stage.isPending ? "Staging…" : "Prepare and preview" })] }), stage.isError && _jsx(Failed, { error: stage.error }), preview && (_jsxs("div", { className: "preview", children: [_jsxs("h3", { children: [preview.label, " \u00B7 ", preview.file_name] }), _jsx("pre", { children: preview.rendered }), preview.requires_confirmation && (_jsx("div", { className: "warning", children: "This upload needs coverage confirmation before it can be accepted. A month absent from the file is treated as not reported, not as every policy having lapsed." })), _jsxs("label", { className: "reason", children: ["Reason (required to reject or roll back)", _jsx("input", { value: reason, onChange: (e) => setReason(e.target.value) })] }), _jsxs("div", { className: "form-row", children: [_jsx("button", { onClick: () => decide.mutate({
                                            path: `/api/uploads/${preview.batch_id}/accept`,
                                            payload: { confirmed_months: preview.coverage?.months?.map((m) => m.forecast_month) ?? null }
                                        }), children: "Accept these exact figures" }), _jsx("button", { disabled: reason.length < 3, onClick: () => decide.mutate({
                                            path: `/api/uploads/${preview.batch_id}/reject`,
                                            payload: { reason }
                                        }), children: "Reject" })] }), decide.isError && _jsx(Failed, { error: decide.error })] }))] }), _jsxs(Panel, { title: "Batch history", children: [q.isLoading && _jsx(Loading, { what: "upload history" }), q.data && (_jsx(DataTable, { caption: "upload batches", rows: q.data.items, columns: [
                            { key: "id", label: "Batch" },
                            { key: "file_name", label: "File" },
                            { key: "file_type", label: "Detected type" },
                            { key: "file_sha256", label: "Hash",
                                render: (r) => _jsx("code", { title: r.file_sha256, children: r.file_sha256.slice(0, 12) }) },
                            { key: "status", label: "Status",
                                render: (r) => _jsx("span", { className: `chip status-${r.status}`, children: r.status }) },
                            { key: "source_row_count", label: "Source rows", align: "right" },
                            { key: "accepted_row_count", label: "Accepted", align: "right" },
                            { key: "duplicate_row_count", label: "Duplicates", align: "right" },
                            { key: "excluded_row_count", label: "Excluded", align: "right" },
                            { key: "rejected_row_count", label: "Rejected", align: "right" },
                            { key: "net_income", label: "Net income", align: "right",
                                render: (r) => money({ value: r.net_income, available: r.net_income !== null }) },
                            { key: "expected_forecast_income", label: "Forecast", align: "right",
                                render: (r) => money({ value: r.expected_forecast_income,
                                    available: r.expected_forecast_income !== null }) },
                            { key: "coverage_start", label: "Coverage",
                                render: (r) => (r.coverage_start ? `${r.coverage_start} to ${r.coverage_end}` : "N/A") },
                            { key: "confirmed_months", label: "Confirmed months",
                                render: (r) => (r.confirmed_months?.length ? r.confirmed_months.length : "\u2014") },
                            { key: "uploaded_by", label: "Uploaded by" },
                            { key: "uploaded_at", label: "Uploaded",
                                render: (r) => new Date(r.uploaded_at).toLocaleString("en-AU") },
                            { key: "rollback_reason", label: "Rollback",
                                render: (r) => r.rollback_reason ?? "\u2014" },
                        ] }))] })] }));
}
