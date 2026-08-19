import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { ChangeBars } from "../components/charts";
import { DataTable, Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";

/* New business.
 *
 * The growth target and its achievement are gone. New business is measured here;
 * the target is a budget concept and lives on the budget page. Carrying both
 * invited the comparison the page kept making -- a whole quarter's target against
 * a part quarter's new business -- which put managers at 7% and 18.5% of a goal
 * for reasons of arithmetic rather than performance.
 *
 * What replaces it is the count. A manager with one large policy and a manager
 * with fifteen small ones read identically before; for new business the count is
 * the activity and the money is the outcome, and both are worth seeing.
 */

export default function NewBusiness() {
  const { years, currentFy } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy;
  const [period, setPeriod] = useState("ytd");

  const params = new URLSearchParams({ financial_year: String(fy ?? "") });
  if (/^q[1-4]$/.test(period)) params.set("quarter", period.slice(1));
  if (period.startsWith("m:")) params.set("month", period.slice(2));

  const q = useQuery({
    queryKey: ["new-business", fy, period],
    queryFn: () => api.newBusiness(params),
    enabled: fy != null,
  });

  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  if (fy == null || !q.data) return <Loading what="new business" />;
  const d = q.data;

  const rows = d.items;
  const sum = (k: string) => rows.reduce((t: number, r: any) => t + Number(r[k] ?? 0), 0);
  const label = period === "ytd" ? "Year to date"
    : period.startsWith("m:") ? monthAU(period.slice(2))
    : `Q${period.slice(1)}`;

  return (
    <>
      <h1>
        New business
        <span className="fy">FY{fy}-{String(fy + 1).slice(2)}</span>
        <select className="inline-select" value={fy}
                onChange={(e) => { setFyPick(Number(e.target.value)); setPeriod("ytd"); }}>
          <YearOptions years={years} />
        </select>
      </h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />

      <div className="quarter-bar">
        <div className="quarter-tabs">
          <button className={`quarter-tab${period === "ytd" ? " is-active" : ""}`}
                  onClick={() => setPeriod("ytd")}>Year to date</button>
          {[1, 2, 3, 4].map((n) => (
            <button key={n} className={`quarter-tab${period === `q${n}` ? " is-active" : ""}`}
                    onClick={() => setPeriod(`q${n}`)}>Q{n}</button>
          ))}
          {/* Only months the year actually has. A fixed twelve would offer
              eleven empty views on a book three months old. */}
          {d.months.map((m: string) => (
            <button key={m} className={`quarter-tab${period === `m:${m}` ? " is-active" : ""}`}
                    onClick={() => setPeriod(`m:${m}`)}>{monthAU(m)}</button>
          ))}
        </div>
      </div>

      <Panel title={`${label} — across the business`}>
        <div className="metric-grid metric-grid-4">
          <Metric label="New business written" emphasis
                  m={{ value: sum("net_new_business"), available: true }}
                  hint="Positive new business, less corrections and cancellations." />
          <Metric label="Transactions" kind="count"
                  m={{ value: sum("new_business_count"), available: true }}
                  hint="New business transactions with positive income." />
          <Metric label="Cancelled"
                  m={{ value: -sum("new_business_cancellations"), available: true }} />
          <Metric label="Corrections"
                  m={{ value: -sum("negative_new_business_corrections"), available: true }} />
        </div>
      </Panel>

      {/* Who is bringing it in. Sorted by value, so the answer is the top bar
          rather than something to be found by reading down a column. */}
      <Panel title={`Net new business by manager — ${label}`}
             subtitle="Positive new business less corrections and cancellations.">
        <ChangeBars limit={20} items={rows.map((r: any) => ({
          label: r.canonical_manager, change: Number(r.net_new_business ?? 0),
        }))} />
      </Panel>

      <Panel title={`${label} — by manager`}>
        <DataTable
          caption="managers"
          rows={rows}
          rowKey={(r: any) => r.canonical_manager}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "new_business_count", label: "Transactions", align: "right",
              render: (r: any) => String(r.new_business_count ?? 0) },
            { key: "gross_new_business", label: "Positive NB", align: "right",
              render: (r: any) => money({ value: r.gross_new_business, available: true }) },
            // Corrections and cancellations are reductions, so they are shown as
            // negatives -- bracketed and red by the table's own formatting. They
            // were previously positive numbers in columns whose headings implied
            // the opposite, which reads as new business until you stop and think.
            { key: "negative_new_business_corrections", label: "NB corrections", align: "right",
              render: (r: any) => money({
                value: r.negative_new_business_corrections
                  ? -Number(r.negative_new_business_corrections) : 0, available: true }) },
            { key: "new_business_cancellations", label: "Cancelled NB", align: "right",
              render: (r: any) => money({
                value: r.new_business_cancellations
                  ? -Number(r.new_business_cancellations) : 0, available: true }) },
            { key: "net_new_business", label: "Net NB", align: "right",
              render: (r: any) => money({ value: r.net_new_business, available: true }) },
          ]}
          serverTotals={{
            canonical_manager: "All managers",
            new_business_count: String(sum("new_business_count")),
            gross_new_business: money({ value: sum("gross_new_business"), available: true }),
            negative_new_business_corrections:
              money({ value: -sum("negative_new_business_corrections"), available: true }),
            new_business_cancellations:
              money({ value: -sum("new_business_cancellations"), available: true }),
            net_new_business: money({ value: sum("net_new_business"), available: true }),
          }}
        />
      </Panel>
    </>
  );
}
