import { useQuery } from "@tanstack/react-query";
import { api, money } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel } from "../components/ui";

export default function Returns() {
  const params = new URLSearchParams();
  const q = useQuery({ queryKey: ["returns"], queryFn: () => api.returnIncome(params) });
  if (q.isLoading) return <Loading what="return income" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  return (
    <>
      <h1>Return income</h1>
      <GstBanner meta={d.meta} />
      <Panel
        title="Where income was returned"
        subtitle="Signed and absolute values are both shown. Signed amounts reduce Net Actual Income; absolute amounts show the size of the leakage."
        actions={<a className="button" href={api.exportUrl("return-income", "csv", params)}>Export CSV</a>}
      >
        <DataTable
          caption="return income categories"
          rows={d.items}
          serverTotals={{
            signed_return_income: money({ value: d.total.signed, available: true }),
            absolute_return_income: money({ value: d.total.absolute, available: true }),
            transaction_rows: d.total.rows,
          }}
          columns={[
            { key: "derived_classification", label: "Classification" },
            { key: "signed_return_income", label: "Signed", align: "right",
              render: (r: any) => money({ value: r.signed_return_income, available: true }) },
            { key: "absolute_return_income", label: "Absolute", align: "right",
              render: (r: any) => money({ value: r.absolute_return_income, available: true }) },
            { key: "transaction_rows", label: "Transactions", align: "right" },
          ]}
        />
      </Panel>
    </>
  );
}
