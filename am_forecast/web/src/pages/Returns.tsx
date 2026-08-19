import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { ChangeBars } from "../components/charts";
import { DataTable, Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";

/* Return income.
 *
 * Two questions, and the page only ever answered one. Grouping by classification
 * says WHAT is being returned; grouping by manager says WHOSE BOOK it is coming
 * out of. The second is the one that leads somewhere -- a concentration in one
 * book is a pattern worth asking about, where a total is just a number to note.
 *
 * So both, side by side, over a period you can narrow.
 */

export default function Returns() {
  const { years, currentFy } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy;
  const [period, setPeriod] = useState("ytd");

  const params = new URLSearchParams({ financial_year: String(fy ?? "") });
  if (/^q[1-4]$/.test(period)) params.set("quarter", period.slice(1));
  if (period.startsWith("m:")) params.set("month", period.slice(2));

  const q = useQuery({
    queryKey: ["returns", fy, period],
    queryFn: () => api.returnIncome(params),
    enabled: fy != null,
  });

  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  if (fy == null || !q.data) return <Loading what="return income" />;
  const d = q.data;

  const label = period === "ytd" ? "Year to date"
    : period.startsWith("m:") ? monthAU(period.slice(2))
    : `Q${period.slice(1)}`;
  const managers = d.by_manager ?? [];
  const worst = managers[0];

  return (
    <>
      <h1>
        Return income
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
          {(d.months ?? []).map((m: string) => (
            <button key={m} className={`quarter-tab${period === `m:${m}` ? " is-active" : ""}`}
                    onClick={() => setPeriod(`m:${m}`)}>{monthAU(m)}</button>
          ))}
        </div>
      </div>

      <Panel title={`${label} — across the business`}>
        <div className="metric-grid metric-grid-3">
          <Metric label="Returned" emphasis
                  m={{ value: -Number(d.total.absolute ?? 0), available: true }}
                  hint="Total return income for the period. Shown as a reduction, because that is what it is." />
          <Metric label="Transactions" kind="count"
                  m={{ value: d.total.rows, available: true }} />
          <Metric label="Largest book"
                  m={{ value: worst ? -Number(worst.absolute_return_income) : null,
                       available: !!worst }}
                  hint={worst ? `${worst.canonical_manager} carries the most return income this period.` : undefined} />
        </div>
        {worst && (
          <p className="footnote">
            <strong>{worst.canonical_manager}</strong> accounts for{" "}
            {money({ value: worst.absolute_return_income, available: true })} of{" "}
            {money({ value: d.total.absolute, available: true })} returned
            {" "}({Math.round(100 * Number(worst.absolute_return_income)
                              / Math.max(1, Number(d.total.absolute)))}%)
            {" "}across {worst.transaction_rows} transactions.
          </p>
        )}
      </Panel>

      {/* Chart and table side by side: the chart answers "who" at a glance, the
          table carries the figures you would quote. Reading one and scrolling to
          the other made comparing them a memory exercise. */}
      <div className="chart-pair">
        <Panel title={`Return income by manager — ${label}`}
               subtitle="Longer bars are larger reductions.">
          <ChangeBars limit={20} items={managers.map((m: any) => ({
            label: m.canonical_manager,
            change: -Number(m.absolute_return_income ?? 0),
          }))} />
        </Panel>
        <Panel title="The same, in figures">
          <DataTable
            caption="managers"
            rows={managers}
            rowKey={(r: any) => r.canonical_manager}
            columns={[
              { key: "canonical_manager", label: "Manager" },
              { key: "transaction_rows", label: "Txns", align: "right",
                render: (r: any) => String(r.transaction_rows ?? 0) },
              { key: "absolute_return_income", label: "Returned", align: "right",
                render: (r: any) => money({
                  value: -Number(r.absolute_return_income ?? 0), available: true }) },
            ]}
            serverTotals={{
              canonical_manager: "All managers",
              transaction_rows: String(d.total.rows ?? 0),
              absolute_return_income: money({
                value: -Number(d.total.absolute ?? 0), available: true }),
            }}
          />
        </Panel>
      </div>

      <Panel title={`What is being returned — ${label}`}
             subtitle="The same total, grouped by class of business rather than by manager.">
        <DataTable
          caption="classes"
          rows={d.items}
          rowKey={(r: any) => r.derived_classification}
          columns={[
            { key: "derived_classification", label: "Class" },
            { key: "transaction_rows", label: "Txns", align: "right",
              render: (r: any) => String(r.transaction_rows ?? 0) },
            { key: "absolute_return_income", label: "Returned", align: "right",
              render: (r: any) => money({
                value: -Number(r.absolute_return_income ?? 0), available: true }) },
          ]}
        />
      </Panel>
    </>
  );
}
