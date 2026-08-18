import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";

/**
 * Forecast history.
 *
 * One timeline per manager. Each accepted Renewals Pending file adds a row,
 * stamped with when it arrived and who loaded it, so "what were we expecting
 * for March, and when did that change?" has a direct answer.
 */
export default function ForecastHistory() {
  const { years, currentFy } = usePeriods();
  const [manager, setManager] = useState("Sam Stewart");
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy ?? new Date().getFullYear();

  const ref = useQuery({ queryKey: ["reference"], queryFn: api.reference });
  const q = useQuery({
    queryKey: ["forecast-history", manager, fy],
    queryFn: () => api.forecastHistory(manager, fy),
  });

  if (q.isLoading) return <Loading what="forecast history" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  return (
    <>
      <h1>
        Forecast history
        <span className="fy">{d.canonical_manager} &middot; {d.financial_year_label}</span>
      </h1>
      <GstBanner meta={d.meta} />
      <div className="purpose">
        <strong>What this page is for.</strong> A record of what was forecast for
        each month, and when. Every accepted Renewals Pending file adds a row,
        time stamped and attributed. Read down a column to see how the
        expectation for that month changed; read across a row to see one
        forecast as it stood on the day it arrived.
      </div>

      <Panel
        title={`${d.entry_count} forecast${d.entry_count === 1 ? "" : "s"} recorded`}
        subtitle="Oldest first. The most recent Renewals Pending file is marked current."
        actions={
          <div className="controls">
            <label>Account manager
              <select value={manager} onChange={(e) => setManager(e.target.value)}>
                {(ref.data?.managers ?? []).map((m: any) => (
                  <option key={m.canonical_manager} value={m.canonical_manager}>
                    {m.canonical_manager}
                  </option>
                ))}
              </select>
            </label>
            <label>Financial year
              <select value={fy} onChange={(e) => setFyPick(Number(e.target.value))}>
                <YearOptions years={years} />
              </select>
            </label>
          </div>
        }
      >
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr>
                <th className="sticky-col">Forecast recorded</th>
                {d.months.map((m: string) => (
                  <th key={m} className="right">{monthAU(m)}</th>
                ))}
                <th className="right total-col">Total</th>
              </tr>
            </thead>
            <tbody>
              {d.entries.map((e: any) => (
                <tr key={e.entry_id}
                    className={e.is_current ? "history-current" : "history-row"}>
                  <td className="sticky-col">
                    <div className="history-label">
                      <strong>{e.label}</strong>
                      {e.is_current && <span className="chip current">current</span>}
                      {e.kind !== "snapshot" && <span className="chip">baseline</span>}
                    </div>
                    <div className="history-meta">
                      {e.recorded_at
                        ? new Date(e.recorded_at).toLocaleString("en-AU", {
                            day: "2-digit", month: "short", year: "numeric",
                            hour: "2-digit", minute: "2-digit",
                            timeZone: "Australia/Melbourne",
                          })
                        : "—"}
                      {e.recorded_by && <> &middot; {e.recorded_by}</>}
                      {e.source_file && <> &middot; <code>{e.source_file}</code></>}
                    </div>
                  </td>
                  {e.cells.map((c: any) => (
                    <td key={c.month} className="right">
                      {c.value === null ? (
                        <span className="not-yet"
                              title="This forecast did not cover this month.">—</span>
                      ) : (
                        <>
                          <span className={Number(c.value) < 0 ? "val negative" : "val"}>
                            {money({ value: c.value, available: true })}
                          </span>
                          {c.change !== null && (
                            <span className={`delta ${Number(c.change) >= 0 ? "up" : "down"}`}
                                  title="Change from the previous forecast">
                              {Number(c.change) >= 0 ? "▲" : "▼"}{" "}
                              {money({ value: Math.abs(Number(c.change)),
                                       available: true })}
                            </span>
                          )}
                          {c.is_new && <span className="chip new">new</span>}
                        </>
                      )}
                    </td>
                  ))}
                  <td className="right total-col">
                    <span className={Number(e.total) < 0 ? "val negative" : "val"}>
                      {e.total === null ? "—" : money({ value: e.total, available: true })}
                    </span>
                    {e.total_change !== null && e.total_change !== undefined && (
                      <span className={`delta ${Number(e.total_change) >= 0 ? "up" : "down"}`}>
                        {Number(e.total_change) >= 0 ? "▲" : "▼"}{" "}
                        {money({ value: Math.abs(Number(e.total_change)),
                                 available: true })}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Notes notes={d.meta.notes} />
      </Panel>
    </>
  );
}
