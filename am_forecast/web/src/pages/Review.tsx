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
    mutationFn: (body: any) => api.post(body.path, body.payload),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["review"] });
                       qc.invalidateQueries({ queryKey: ["review-history"] });
                       setReason(""); },
  });

  if (q.isLoading) return <Loading what="the review queue" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const c = d.counts;

  return (
    <>
      <h1>Matching review queue</h1>
      <GstBanner meta={d.meta} />

      <Panel title="Queue composition"
             subtitle="Only the first group needs individual decisions. The other two are bulk artefacts with a known cause and are separated so they cannot bury the real exceptions.">
        <div className="metric-grid">
          <div className="metric metric-emphasis"><div className="metric-label">Needs a decision</div>
            <div className="metric-value">{c.actionable}</div>
            <div className="metric-sub">{d.explanations.actionable}</div></div>
          <div className="metric"><div className="metric-label">July timing artefacts</div>
            <div className="metric-value">{c.july_timing_artefacts}</div>
            <div className="metric-sub">{d.explanations.july_timing_artefacts}</div></div>
          <div className="metric"><div className="metric-label">Outside matching scope</div>
            <div className="metric-value">{c.out_of_scope}</div>
            <div className="metric-sub">{d.explanations.out_of_scope}</div></div>
        </div>
      </Panel>

      <Panel title="Candidates"
             actions={
               <div className="segmented">
                 {TABS.map((t) => (
                   <button key={t.key} className={kind === t.key ? "on" : ""}
                           onClick={() => setKind(t.key)}>{t.label}</button>
                 ))}
               </div>
             }>
        {kind !== "actionable" && (
          <div className="warning">
            These are explained in bulk and are not individual errors.
            {kind === "timing"
              ? " The Renewals Pending file was extracted after most July renewals had transacted, so there is no forecast policy to match them against."
              : " These renewals fall in months with no policy-grain forecast, chiefly FY2025-26. There was never a forecast to match them to."}
          </div>
        )}
        <label className="reason">
          Reason (required for any decision)
          <input value={reason} onChange={(e) => setReason(e.target.value)}
                 placeholder="e.g. verified against the underwriter schedule" />
        </label>
        <DataTable
          caption="review candidates"
          rows={d.items}
          columns={[
            { key: "reason", label: "Why" },
            { key: "transaction_id", label: "Txn" },
            { key: "txn_client", label: "Txn client" },
            { key: "txn_policy_number", label: "Txn policy" },
            { key: "txn_category", label: "Cat" },
            { key: "transaction_date", label: "Date",
              render: (r: any) => dateAU(r.transaction_date?.slice(0, 10)) },
            { key: "txn_income", label: "Income", align: "right",
              render: (r: any) => money({ value: r.txn_income, available: r.txn_income !== null }) },
            { key: "policy_id", label: "PolicyID" },
            { key: "policy_class", label: "Policy class" },
            { key: "tier", label: "Tier", align: "right" },
            { key: "actions", label: "Decision",
              render: (r: any) => (kind === "actionable" && r.policy_id ? (
                <span className="row-actions">
                  <button disabled={reason.length < 3 || decide.isPending}
                          onClick={() => decide.mutate({ path: "/api/review/match",
                            payload: { policy_id: r.policy_id, forecast_month: r.forecast_month,
                                       transaction_id: r.transaction_id, reason } })}>
                    Match
                  </button>
                  <button disabled={reason.length < 3 || decide.isPending}
                          onClick={() => decide.mutate({ path: "/api/review/reject",
                            payload: { transaction_id: r.transaction_id,
                                       policy_id: r.policy_id, reason } })}>
                    Reject
                  </button>
                </span>
              ) : "\u2014") },
          ]}
        />
        {decide.isError && <Failed error={decide.error} />}
      </Panel>

      <Panel title="Decision history"
             subtitle="Every manual decision keeps its reviewer, timestamp, reason, previous decision and new decision.">
        {history.data && (
          <DataTable
            caption="decisions"
            rows={history.data.items}
            columns={[
              { key: "decided_at", label: "When",
                render: (r: any) => new Date(r.decided_at).toLocaleString("en-AU") },
              { key: "reviewer", label: "Reviewer" },
              { key: "action", label: "Action" },
              { key: "policy_id", label: "PolicyID" },
              { key: "transaction_id", label: "Txn" },
              { key: "reason", label: "Reason" },
              { key: "previous_decision", label: "Replaced",
                render: (r: any) => (r.previous_decision ? "Yes" : "\u2014") },
            ]}
          />
        )}
      </Panel>
    </>
  );
}
