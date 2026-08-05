import { useQuery } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";

export default function Movement() {
  const params = new URLSearchParams();
  const q = useQuery({ queryKey: ["movement"], queryFn: () => api.forecastMovement(params) });
  if (q.isLoading) return <Loading what="forecast movement" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const t = d.totals;

  return (
    <>
      <h1>Forecast movement <span className="fy">Original to Latest</span></h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />

      <Panel title="Movement summary"
             subtitle="A removed policy reduces Latest Forecast and is reported here. It never creates negative forecast income.">
        <div className="metric-grid">
          <div className="metric"><div className="metric-label">Policies removed</div>
            <div className="metric-value">{t.policies_removed}</div>
            <div className="metric-sub">{money({ value: t.income_removed, available: true })} removed</div></div>
          <div className="metric"><div className="metric-label">Policies added</div>
            <div className="metric-value">{t.policies_added}</div>
            <div className="metric-sub">{money({ value: t.income_added, available: true })} added</div></div>
          <div className="metric"><div className="metric-label">Amount changes</div>
            <div className="metric-value">{money({ value: t.amount_changes, available: true })}</div></div>
          <div className="metric"><div className="metric-label">Manager transfers
              <span className="hint" title="Counted from the independent manager-change flag, so a policy that also changed amount is still counted here.">i</span></div>
            <div className="metric-value">{t.manager_transfers}</div></div>
          <div className="metric"><div className="metric-label">Detail changes</div>
            <div className="metric-value">{t.detail_changes}</div></div>
          <div className="metric"><div className="metric-label">Several changes at once</div>
            <div className="metric-value">{t.multi_attribute_changes}</div>
            <div className="metric-sub">policies carrying more than one change</div></div>
          <div className="metric metric-emphasis"><div className="metric-label">Net forecast movement</div>
            <div className="metric-value">{money({ value: t.net_forecast_movement, available: true })}</div></div>
        </div>
      </Panel>

      <Panel title="By month and manager">
        <DataTable
          caption="movement"
          rows={d.summary}
          columns={[
            { key: "forecast_month", label: "Month", render: (r: any) => monthAU(r.forecast_month) },
            { key: "canonical_manager", label: "Manager" },
            { key: "policies_removed", label: "Removed", align: "right" },
            { key: "expected_income_removed", label: "Income removed", align: "right",
              render: (r: any) => money({ value: r.expected_income_removed, available: true }) },
            { key: "policies_added", label: "Added", align: "right" },
            { key: "expected_income_added", label: "Income added", align: "right",
              render: (r: any) => money({ value: r.expected_income_added, available: true }) },
            { key: "amount_changes", label: "Amount change", align: "right",
              render: (r: any) => money({ value: r.amount_changes, available: true }) },
            { key: "manager_transfers", label: "Transfers", align: "right" },
            { key: "detail_changes", label: "Detail changes", align: "right" },
            { key: "multi_attribute_changes", label: "Multi-change", align: "right" },
          ]}
        />
      </Panel>

      <Panel title="Policy detail"
             subtitle={`${d.detail.total} movement records. Every summary figure drills to these rows.`}
             actions={<a className="button" href={api.exportUrl("forecast-movement", "csv", params)}>Export CSV</a>}>
        <DataTable
          caption="policy movements"
          rows={d.detail.items}
          columns={[
            { key: "policy_id", label: "PolicyID" },
            { key: "forecast_month", label: "Month", render: (r: any) => monthAU(r.forecast_month) },
            { key: "client_code", label: "Client" },
            { key: "policy_number", label: "Policy number" },
            { key: "movement_type", label: "Primary change" },
            { key: "secondary_changes", label: "All changes",
              render: (r: any) => (r.secondary_changes?.length ? r.secondary_changes.join(", ") : "\u2014") },
            { key: "previous_income", label: "Previous", align: "right",
              render: (r: any) => money({ value: r.previous_income, available: true }) },
            { key: "latest_income", label: "Latest", align: "right",
              render: (r: any) => money({ value: r.latest_income, available: true }) },
            { key: "movement_amount", label: "Movement", align: "right",
              render: (r: any) => money({ value: r.movement_amount, available: true }) },
            { key: "canonical_from_manager", label: "From" },
            { key: "canonical_to_manager", label: "To" },
          ]}
        />
      </Panel>
    </>
  );
}
