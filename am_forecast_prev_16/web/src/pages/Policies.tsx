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
  if (outcome) params.set("outcome", outcome);
  if (client) params.set("client", client);

  const q = useQuery({
    queryKey: ["policies", outcome, client, page],
    queryFn: () => api.policies(params),
  });
  if (q.isLoading) return <Loading what="policy renewals" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  return (
    <>
      <h1>Policy renewals <span className="fy">retention tracking</span></h1>
      <GstBanner meta={d.meta} />
      <div className="purpose">
        <strong>What this page is for.</strong> Every policy you were forecast to
        renew, and what actually happened to it: renewed, transferred, lapsed, or
        still pending. Use it to chase renewals before they lapse, and to see
        which clients and classes you are losing. Filter by outcome to get a
        working list.
      </div>
      <Notes notes={d.meta.notes} />
      <Panel
        title={`${d.total.toLocaleString()} forecast policies`}
        subtitle="Renewal income is RWL and TRW only. Total associated income includes every line attached to the policy and answers a different question."
        actions={
          <div className="controls">
            <label>Outcome
              <select value={outcome} onChange={(e) => { setOutcome(e.target.value); setPage(0); }}>
                {OUTCOMES.map((o) => <option key={o} value={o}>{o || "All"}</option>)}
              </select>
            </label>
            <label>Client
              <input value={client} placeholder="code"
                     onChange={(e) => { setClient(e.target.value); setPage(0); }} />
            </label>
            <a className="button" href={api.exportUrl("policies", "csv", params)}>Export CSV</a>
            <a className="button" href={api.exportUrl("policies", "xlsx", params)}>Export XLSX</a>
          </div>
        }
      >
        <DataTable
          caption="policies"
          rows={d.items}
          columns={[
            { key: "policy_id", label: "PolicyID" },
            { key: "client_code", label: "Client" },
            { key: "policy_number", label: "Policy number" },
            { key: "class_abbrev", label: "Class" },
            { key: "underwriter_abbrev", label: "Underwriter" },
            { key: "expiry_date", label: "Expiry", render: (r: any) => dateAU(r.expiry_date) },
            { key: "forecast_month", label: "Month", render: (r: any) => monthAU(r.forecast_month) },
            { key: "original_manager", label: "Source manager" },
            { key: "canonical_manager", label: "Canonical manager" },
            { key: "original_forecast_income", label: "Original", align: "right",
              render: (r: any) => money({ value: r.original_forecast_income, available: true }) },
            { key: "latest_forecast_income", label: "Latest", align: "right",
              render: (r: any) => money({ value: r.latest_forecast_income,
                                          available: r.latest_forecast_income !== null,
                                          reason: "Completed month: reports actuals, no Latest Forecast." }) },
            { key: "forecast_movement", label: "Movement", align: "right",
              render: (r: any) => money({ value: r.forecast_movement,
                                          available: r.forecast_movement !== null }) },
            { key: "renewal_transaction_income", label: "Renewal income", align: "right",
              render: (r: any) => money({ value: r.renewal_transaction_income, available: true }) },
            { key: "total_associated_income", label: "Total associated", align: "right",
              render: (r: any) => money({ value: r.total_associated_income, available: true }) },
            { key: "outcome", label: "Outcome",
              render: (r: any) => <span className={`chip outcome-${r.outcome}`}>{r.outcome}</span> },
            { key: "best_tier", label: "Tier", align: "right",
              render: (r: any) => (r.best_tier ?? "N/A") },
            { key: "confidence", label: "Confidence", align: "right",
              render: (r: any) => (r.confidence === null ? "N/A"
                                   : percent({ value: r.confidence, available: true }, 0)) },
            { key: "requires_review", label: "Review",
              render: (r: any) => (r.requires_review ? "Yes" : "\u2014") },
            { key: "exception_flags", label: "Exceptions",
              render: (r: any) => (r.exception_flags?.length ? r.exception_flags.join(", ") : "\u2014") },
          ]}
        />
        <div className="pager">
          <button disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</button>
          <span>Rows {page * limit + 1}\u2013{Math.min((page + 1) * limit, d.total)} of {d.total.toLocaleString()}</span>
          <button disabled={(page + 1) * limit >= d.total} onClick={() => setPage((p) => p + 1)}>Next</button>
        </div>
      </Panel>
    </>
  );
}
