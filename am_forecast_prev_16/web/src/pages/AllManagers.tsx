import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { api, money, monthAU, percent } from "../lib/api";
import { Failed, GstBanner, Loading, Panel } from "../components/ui";

const MEASURES = [
  { key: "net_actual", label: "Net Actual", kind: "money" },
  { key: "budget", label: "Total Budget", kind: "money" },
  { key: "variance", label: "Variance to Budget", kind: "money" },
  { key: "achievement", label: "Budget Achievement", kind: "percent" },
  { key: "original_forecast", label: "Renewal Forecast", kind: "money" },
];

export default function AllManagers() {
  const { years, currentFy, label: fyLabelOf } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  // Default to the current financial year once the data says what it is.
  const fy = fyPick ?? currentFy ?? new Date().getFullYear();
  const setFy = setFyPick;
  const [measure, setMeasure] = useState("net_actual");
  const [showAll, setShowAll] = useState(false);
  const q = useQuery({
    queryKey: ["matrix", fy, measure, showAll],
    queryFn: () => api.managerMatrix(fy, measure, showAll),
  });

  if (q.isLoading) return <Loading what="the manager matrix" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;
  const kind = MEASURES.find((m) => m.key === measure)!.kind;

  const fmt = (v: any, status: string) => {
    if (status === "future") {
      return <span className="not-yet" title="This month has not started yet.">—</span>;
    }
    if (v === null) {
      return <span className="na" title="Not available for this period.">N/A</span>;
    }
    return kind === "percent"
      ? percent({ value: v, available: true })
      : money({ value: v, available: true });
  };

  const cellTone = (v: any, status: string) => {
    if (status !== "actual" || v === null) return "";
    if (measure === "variance") return Number(v) >= 0 ? " cell-good" : " cell-bad";
    if (measure === "achievement") return Number(v) >= 1 ? " cell-good" : " cell-bad";
    return "";
  };

  return (
    <>
      <h1>All managers by month <span className="fy">{fyLabelOf(fy)}</span></h1>
      <GstBanner meta={d.meta} />
      <Panel
        title={MEASURES.find((m) => m.key === measure)!.label}
        subtitle="Every manager down the side, every month across the top. One measure at a time, so no figure can be mistaken for another."
        actions={
          <div className="controls">
            <label>Measure
              <select value={measure} onChange={(e) => setMeasure(e.target.value)}>
                {MEASURES.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
              </select>
            </label>
            <label>Financial year
              <select value={fy} onChange={(e) => setFy(Number(e.target.value))}>
                <YearOptions years={years} />
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={showAll}
                     onChange={(e) => setShowAll(e.target.checked)} />
              Include non-ranked
            </label>
          </div>
        }
      >
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="sticky-col">Account manager</th>
                {d.months.map((m: string, i: number) => (
                  <th key={m} className={`right${d.month_status[i] === "future" ? " future-col" : ""}`}>
                    {monthAU(m)}
                  </th>
                ))}
                {kind !== "percent" && <th className="right total-col">Total</th>}
              </tr>
            </thead>
            <tbody>
              {d.rows.map((r: any) => (
                <tr key={r.canonical_manager}>
                  <td className="sticky-col">
                    {r.canonical_manager}
                    {!r.include_in_rankings && <span className="chip">not ranked</span>}
                  </td>
                  {r.cells.map((c: any, i: number) => (
                    <td key={c.month}
                        className={`right${d.month_status[i] === "future" ? " future-col" : ""}${cellTone(c.value, c.status)}`}>
                      {fmt(c.value, c.status)}
                    </td>
                  ))}
                  {kind !== "percent" && (
                    <td className="right total-col">
                      {r.total === null ? "—" : money({ value: r.total, available: true })}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
            {kind !== "percent" && (
              <tfoot>
                <tr>
                  <td className="sticky-col">All managers</td>
                  {d.column_totals.map((c: any, i: number) => (
                    <td key={c.month} className={`right${d.month_status[i] === "future" ? " future-col" : ""}`}>
                      {fmt(c.value, c.status)}
                    </td>
                  ))}
                  <td className="right total-col">
                    {d.grand_total === null ? "—" : money({ value: d.grand_total, available: true })}
                  </td>
                </tr>
              </tfoot>
            )}
          </table>
        </div>
      </Panel>
    </>
  );
}
