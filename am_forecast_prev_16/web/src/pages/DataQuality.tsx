import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, monthAU } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel } from "../components/ui";

const INDICATORS: { key: string; label: string; drill?: string; hint?: string }[] = [
  { key: "negative_expected_policies", label: "Negative expected income",
    drill: "negative_expected_policies",
    hint: "Retain their raw negative amount, contribute zero to the forecast, and never reduce a monthly forecast below zero." },
  { key: "zero_expected_policies", label: "Zero expected income",
    drill: "zero_expected_policies" },
  { key: "overdue_pending_policies", label: "Overdue pending",
    drill: "overdue_pending_policies",
    hint: "Expiry precedes the snapshot date. Retained in their original renewal month, never moved forward." },
  { key: "residual_pending_policies", label: "Residual pending",
    drill: "residual_pending_policies",
    hint: "Sit in a period already closed at the reporting cut-off." },
  { key: "unmapped_managers", label: "Unmapped managers" },
  { key: "unmapped_categories", label: "Unmapped categories" },
  { key: "unmapped_class_equivalences", label: "Unmapped class equivalences",
    hint: "Renewals classes with no mapping to a sales class. They cannot reach the top matching tier but never block a policy-number match." },
  { key: "restated_transactions", label: "Restated transactions",
    drill: "restated_transactions",
    hint: "A fingerprint reappeared with different supporting values. Held for review, never overwritten silently." },
  { key: "ambiguous_matches", label: "Ambiguous matches" },
  { key: "allocation_breaches", label: "Allocation breaches",
    drill: "allocation_breaches",
    hint: "Must always be zero. A transaction may never be credited beyond its own income." },
  { key: "unavailable_baselines", label: "Unavailable baselines" },
  { key: "partial_financial_years", label: "Partial financial years" },
  { key: "excluded_sales_records", label: "Highview-excluded transactions",
    drill: "excluded_records",
    hint: "Excluded from every reported total, retained in full for audit." },
  { key: "excluded_forecast_records", label: "Highview-excluded policies",
    drill: "excluded_records" },
];

export default function DataQuality() {
  const [drill, setDrill] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["dq"], queryFn: api.dataQuality });
  const detail = useQuery({
    queryKey: ["dq-detail", drill],
    queryFn: () => api.dataQualityDetail(drill!),
    enabled: !!drill,
  });

  if (q.isLoading) return <Loading what="data quality" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  return (
    <>
      <h1>Data quality and reconciliation</h1>
      <GstBanner meta={d.meta} />

      <Panel title="Indicators" subtitle="Every indicator with a drill-down opens the underlying records.">
        <div className="indicator-grid">
          {INDICATORS.map((ind) => {
            const value = d.counts[ind.key];
            const expected = d.expected[ind.key];
            const mismatch = expected !== undefined && expected !== value;
            return (
              <button key={ind.key}
                      className={`indicator${ind.drill ? " drillable" : ""}${mismatch ? " mismatch" : ""}`}
                      onClick={() => ind.drill && setDrill(ind.drill)}
                      title={ind.hint}>
                <span className="indicator-value">{value}</span>
                <span className="indicator-label">{ind.label}</span>
                {expected !== undefined && (
                  <span className="indicator-expected">
                    expected {expected}{mismatch ? " \u2014 mismatch" : " \u2713"}
                  </span>
                )}
              </button>
            );
          })}
        </div>
        <p className="footnote">{d.notes.zero_expected_policies}</p>
      </Panel>

      <Panel title="Forecast baselines"
             subtitle="A month that is not complete reports N/A rather than zero, and managers listed as exceptions report N/A even where the month itself is usable.">
        <DataTable
          caption="baselines"
          rows={d.baselines}
          columns={[
            { key: "forecast_month", label: "Month", render: (r: any) => monthAU(r.forecast_month) },
            { key: "baseline_status", label: "Status" },
            { key: "baseline_source", label: "Source", render: (r: any) => r.baseline_source ?? "N/A" },
            { key: "suppress_achievement", label: "Achievement suppressed",
              render: (r: any) => (r.suppress_achievement ? "Yes" : "\u2014") },
            { key: "manager_exceptions", label: "Manager exceptions",
              render: (r: any) => (r.manager_exceptions?.length ? r.manager_exceptions.join(", ") : "\u2014") },
            { key: "note", label: "Note", render: (r: any) => r.note ?? "\u2014" },
          ]}
        />
      </Panel>

      <Panel title="Partial period coverage"
             subtitle="A fragment is never presented as a full financial year.">
        <DataTable
          caption="partial periods"
          rows={d.partial_periods}
          columns={[
            { key: "financial_year", label: "FY", render: (r: any) => `FY${r.financial_year}-${String(r.financial_year + 1).slice(2)}` },
            { key: "data_domain", label: "Domain" },
            { key: "months_present", label: "Months", align: "right" },
            { key: "label", label: "Note" },
          ]}
        />
      </Panel>

      {drill && (
        <Panel title={`Drill-down: ${drill.replace(/_/g, " ")}`}
               actions={<button onClick={() => setDrill(null)}>Close</button>}>
          {detail.isLoading && <Loading what="records" />}
          {detail.data && (
            <DataTable
              caption="records"
              rows={detail.data.items}
              columns={Object.keys(detail.data.items[0] ?? {}).map((k) => ({
                key: k, label: k.replace(/_/g, " "),
                render: (r: any) => (r[k] === null ? "N/A" : String(r[k])),
              }))}
            />
          )}
        </Panel>
      )}
    </>
  );
}
