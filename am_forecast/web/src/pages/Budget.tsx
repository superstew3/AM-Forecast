import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, money, monthAU, percent } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";

export default function Budget() {
  const qc = useQueryClient();
  const [scope, setScope] = useState("global");
  const [manager, setManager] = useState("");
  const [quarter, setQuarter] = useState("");
  const [pct, setPct] = useState("0.075");
  const [dollars, setDollars] = useState("");
  const [reason, setReason] = useState("");

  const q = useQuery({ queryKey: ["budget", 2026], queryFn: () => api.budget(2026) });
  const audit = useQuery({ queryKey: ["budget-audit"], queryFn: api.budgetAudit });
  const save = useMutation({
    mutationFn: (body: any) => api.post("/api/budget/growth-rate", body),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["budget"] });
                       qc.invalidateQueries({ queryKey: ["budget-audit"] });
                       setReason(""); },
  });

  if (q.isLoading) return <Loading what="budget" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  return (
    <>
      <h1>Budget <span className="fy">FY2026-27</span></h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />

      <Panel title="Adjust the growth assumption"
             subtitle="Resolution is most-specific-first: manager and quarter, then manager, then global. A dollar override supersedes the percentage at its level.">
        <div className="form-row">
          <label>Scope
            <select value={scope} onChange={(e) => setScope(e.target.value)}>
              <option value="global">Global</option>
              <option value="manager">Manager</option>
              <option value="manager_quarter">Manager and quarter</option>
            </select>
          </label>
          {scope !== "global" && (
            <label>Manager
              <input value={manager} onChange={(e) => setManager(e.target.value)}
                     placeholder="Sam Stewart" />
            </label>
          )}
          {scope === "manager_quarter" && (
            <label>Quarter
              <select value={quarter} onChange={(e) => setQuarter(e.target.value)}>
                <option value="">…</option>
                {[1, 2, 3, 4].map((n) => <option key={n} value={n}>Q{n}</option>)}
              </select>
            </label>
          )}
          <label>Growth %
            <input value={pct} onChange={(e) => setPct(e.target.value)} placeholder="0.075" />
          </label>
          <label>Dollar override
            <input value={dollars} onChange={(e) => setDollars(e.target.value)}
                   placeholder="optional" />
          </label>
          <label className="grow">Reason
            <input value={reason} onChange={(e) => setReason(e.target.value)}
                   placeholder="why this assumption changed" />
          </label>
          <button disabled={reason.length < 3 || save.isPending}
                  onClick={() => save.mutate({
                    scope,
                    canonical_manager: scope === "global" ? null : manager,
                    financial_year: scope === "manager_quarter" ? 2026 : null,
                    financial_quarter: scope === "manager_quarter" ? Number(quarter) : null,
                    growth_pct: dollars ? null : Number(pct),
                    dollar_override: dollars ? Number(dollars) : null,
                    reason,
                  })}>
            Save assumption
          </button>
        </div>
        {save.isError && <Failed error={save.error} />}
        <p className="footnote">
          Changing the Latest Forecast never changes the Original Forecast or the
          Budget. A lapse, a removal or a returned cancellation reduces actual
          performance and outlook, not the target.
        </p>
      </Panel>

      <Panel title="Quarterly budget"
             subtitle="The active assumption and the level of the hierarchy that supplied it are shown on every row.">
        <DataTable
          caption="quarterly budget"
          rows={d.quarters}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "financial_quarter", label: "Qtr", render: (r: any) => `Q${r.financial_quarter}` },
            { key: "original_renewal_forecast", label: "Original Forecast", align: "right",
              render: (r: any) => money({ value: r.original_renewal_forecast, available: true }) },
            { key: "growth_basis", label: "Assumption from",
              render: (r: any) => <span className="chip">{r.growth_basis}</span> },
            { key: "growth_pct", label: "Growth %", align: "right",
              render: (r: any) => (r.growth_pct === null ? "N/A"
                                   : percent({ value: r.growth_pct, available: true })) },
            { key: "dollar_override", label: "Dollar override", align: "right",
              render: (r: any) => money({ value: r.dollar_override,
                                          available: r.dollar_override !== null,
                                          reason: "No dollar override; the percentage is active." }) },
            { key: "new_business_growth_target", label: "NB target", align: "right",
              render: (r: any) => money({ value: r.new_business_growth_target, available: true }) },
            { key: "total_budget", label: "Total Budget", align: "right",
              render: (r: any) => money({ value: r.total_budget, available: true }) },
          ]}
        />
      </Panel>

      <Panel title="Monthly allocation"
             subtitle="The quarterly target is spread by each month's share of that quarter's Original Renewal Forecast, not in equal thirds.">
        <DataTable
          caption="monthly budget"
          rows={d.monthly}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "forecast_month", label: "Month", render: (r: any) => monthAU(r.forecast_month) },
            { key: "original_forecast", label: "Original Forecast", align: "right",
              render: (r: any) => money({ value: r.original_forecast, available: true }) },
            { key: "allocation_method", label: "Allocation" },
            { key: "calculated_growth_target", label: "Calculated", align: "right",
              render: (r: any) => money({ value: r.calculated_growth_target, available: true }) },
            { key: "override_amount", label: "Override", align: "right",
              render: (r: any) => money({ value: r.override_amount,
                                          available: r.override_amount !== null,
                                          reason: "No monthly override; the calculated value is active." }) },
            { key: "new_business_growth_target", label: "Final", align: "right",
              render: (r: any) => money({ value: r.new_business_growth_target, available: true }) },
            { key: "override_reason", label: "Reason",
              render: (r: any) => r.override_reason ?? "\u2014" },
            { key: "total_budget", label: "Total Budget", align: "right",
              render: (r: any) => money({ value: r.total_budget, available: true }) },
          ]}
        />
      </Panel>

      <Panel title="Budget audit history">
        {audit.data && (
          <DataTable
            caption="budget changes"
            rows={audit.data.items}
            columns={[
              { key: "performed_at", label: "When",
                render: (r: any) => new Date(r.performed_at).toLocaleString("en-AU") },
              { key: "performed_by", label: "User" },
              { key: "action", label: "Action" },
              { key: "scope_description", label: "Scope" },
              { key: "canonical_manager", label: "Manager",
                render: (r: any) => r.canonical_manager ?? "all" },
              { key: "before_value", label: "Before",
                render: (r: any) => (r.before_value ? JSON.stringify(r.before_value) : "\u2014") },
              { key: "after_value", label: "After",
                render: (r: any) => JSON.stringify(r.after_value) },
              { key: "reason", label: "Reason" },
            ]}
          />
        )}
      </Panel>
    </>
  );
}
