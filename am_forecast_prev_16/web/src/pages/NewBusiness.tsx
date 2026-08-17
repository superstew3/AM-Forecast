import { useQuery } from "@tanstack/react-query";
import { api, money } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel, Value } from "../components/ui";

export default function NewBusiness() {
  const params = new URLSearchParams();
  const q = useQuery({ queryKey: ["newbusiness"], queryFn: () => api.newBusiness(params) });
  if (q.isLoading) return <Loading what="new business" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  return (
    <>
      <h1>New business <span className="fy">FY2026-27</span></h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />
      <Panel title="New business against growth target"
             subtitle="The growth target is a budget only. It is never added to Original Forecast, Latest Forecast or Latest Outlook.">
        <DataTable
          caption="new business"
          rows={d.items}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "financial_quarter", label: "Qtr",
              render: (r: any) => `Q${r.financial_quarter}` },
            { key: "gross_new_business", label: "Positive NB", align: "right",
              render: (r: any) => money({ value: r.gross_new_business, available: true }) },
            { key: "negative_new_business_corrections", label: "NB corrections", align: "right",
              render: (r: any) => money({ value: r.negative_new_business_corrections, available: true }) },
            { key: "new_business_cancellations", label: "Cancelled NB", align: "right",
              render: (r: any) => money({ value: r.new_business_cancellations, available: true }) },
            { key: "net_new_business", label: "Net NB", align: "right",
              render: (r: any) => money({ value: r.net_new_business, available: true }) },
            { key: "new_business_growth_target", label: "Growth target", align: "right",
              render: (r: any) => money({ value: r.new_business_growth_target, available: true }) },
            { key: "growth_target_achievement", label: "Achievement", align: "right",
              render: (r: any) => <Value m={r.growth_target_achievement} kind="percent" /> },
          ]}
        />
      </Panel>
    </>
  );
}
