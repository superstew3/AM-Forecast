import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, money } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel } from "../components/ui";

export default function Uploads() {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<any>(null);
  const [reason, setReason] = useState("");
  const q = useQuery({ queryKey: ["uploads"], queryFn: api.uploads });

  const stage = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch("/api/uploads/prepare", {
        method: "POST", body: form,
        headers: { "X-User": "sam", "X-Role": "administrator" },
      });
      if (!res.ok) throw new Error((await res.json()).detail ?? "Upload failed");
      return res.json();
    },
    onSuccess: (data) => { setPreview(data); qc.invalidateQueries({ queryKey: ["uploads"] }); },
  });

  const decide = useMutation({
    mutationFn: ({ path, payload }: any) => api.post(path, payload),
    onSuccess: () => { setPreview(null); qc.invalidateQueries(); },
  });

  /**
   * A pending batch stays actionable from history.
   *
   * Accept and Reject used to appear only beside a freshly staged preview, so
   * anything that cleared the screen — a reload, changing role — stranded the
   * batch as pending with no way to act on it.
   */
  const actions = (r: any) => {
    if (r.status === "pending") {
      return (
        <span className="row-actions">
          <button onClick={() => decide.mutate({
                    path: `/api/uploads/${r.id}/accept`, payload: {} })}>
            Accept
          </button>
          <button disabled={reason.length < 3}
                  title={reason.length < 3 ? "Enter a reason above first" : undefined}
                  onClick={() => decide.mutate({
                    path: `/api/uploads/${r.id}/reject`, payload: { reason } })}>
            Reject
          </button>
        </span>
      );
    }
    if (r.status === "accepted") {
      return (
        <button disabled={reason.length < 3}
                title={reason.length < 3 ? "Enter a reason above first" : undefined}
                onClick={() => decide.mutate({
                  path: `/api/uploads/${r.id}/rollback`, payload: { reason } })}>
          Roll back
        </button>
      );
    }
    return <span className="not-yet">—</span>;
  };

  return (
    <>
      <h1>Uploads and audit history</h1>
      <GstBanner />

      <Panel title="Upload a report"
             subtitle="Prepare stages and previews the file without touching any reported figure. The numbers below are exactly what will land on accept.">
        <div className="form-row">
          <input type="file" ref={fileRef} accept=".csv,.xlsx" />
          <button disabled={stage.isPending}
                  onClick={() => { const f = fileRef.current?.files?.[0];
                                   if (f) stage.mutate(f); }}>
            {stage.isPending ? "Staging…" : "Prepare and preview"}
          </button>
        </div>
        {stage.isError && <Failed error={stage.error} />}
        {preview && (
          <div className="preview">
            <h3>{preview.label} &middot; {preview.file_name}</h3>
            <pre>{preview.rendered}</pre>
            {preview.requires_confirmation && (
              <div className="warning">
                This upload needs coverage confirmation before it can be accepted.
                A month absent from the file is treated as not reported, not as
                every policy having lapsed.
              </div>
            )}
            <label className="reason">
              Reason (required to reject or roll back)
              <input value={reason} onChange={(e) => setReason(e.target.value)} />
            </label>
            <div className="form-row">
              <button onClick={() => decide.mutate({
                        path: `/api/uploads/${preview.batch_id}/accept`,
                        payload: { confirmed_months:
                          preview.coverage?.months?.map((m: any) => m.forecast_month) ?? null } })}>
                Accept these exact figures
              </button>
              <button disabled={reason.length < 3}
                      onClick={() => decide.mutate({
                        path: `/api/uploads/${preview.batch_id}/reject`,
                        payload: { reason } })}>
                Reject
              </button>
            </div>
            {decide.isError && <Failed error={decide.error} />}
          </div>
        )}
      </Panel>

      <Panel title="Batch history"
             subtitle="A pending batch can be accepted or rejected from here at any time. An accepted batch can be rolled back; rollback reverses it exactly and leaves other uploads untouched.">
        <label className="reason">
          Reason (required to reject or roll back)
          <input value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        {q.isLoading && <Loading what="upload history" />}
        {q.data && (
          <DataTable
            caption="upload batches"
            rows={q.data.items}
            columns={[
              { key: "id", label: "Batch" },
              { key: "file_name", label: "File" },
              { key: "file_type", label: "Detected type" },
              { key: "file_sha256", label: "Hash",
                render: (r: any) => <code title={r.file_sha256}>{r.file_sha256.slice(0, 12)}</code> },
              { key: "status", label: "Status",
                render: (r: any) => <span className={`chip status-${r.status}`}>{r.status}</span> },
              { key: "source_row_count", label: "Source rows", align: "right" },
              { key: "accepted_row_count", label: "Accepted", align: "right" },
              { key: "duplicate_row_count", label: "Duplicates", align: "right" },
              { key: "excluded_row_count", label: "Excluded", align: "right" },
              { key: "rejected_row_count", label: "Rejected", align: "right" },
              { key: "net_income", label: "Net income", align: "right",
                render: (r: any) => money({ value: r.net_income, available: r.net_income !== null }) },
              { key: "expected_forecast_income", label: "Forecast", align: "right",
                render: (r: any) => money({ value: r.expected_forecast_income,
                                            available: r.expected_forecast_income !== null }) },
              { key: "coverage_start", label: "Coverage",
                render: (r: any) => (r.coverage_start ? `${r.coverage_start} to ${r.coverage_end}` : "N/A") },
              { key: "confirmed_months", label: "Confirmed months",
                render: (r: any) => (r.confirmed_months?.length ? r.confirmed_months.length : "\u2014") },
              { key: "uploaded_by", label: "Uploaded by" },
              { key: "uploaded_at", label: "Uploaded",
                render: (r: any) => new Date(r.uploaded_at).toLocaleString("en-AU") },
              { key: "rollback_reason", label: "Rollback",
                render: (r: any) => r.rollback_reason ?? "\u2014" },
              { key: "actions", label: "Action", render: actions },
            ]}
          />
        )}
      </Panel>
    </>
  );
}
