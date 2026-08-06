import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Cell, GridRow, api, cell, cellTitle, money, monthAU, percent,
} from "../lib/api";
import { BudgetGauge, MonthlyBars } from "../components/charts";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";

/** How a row should be read. Declared by the API, not guessed from the label. */
function kindOf(row: GridRow): "money" | "percent" | "count" | "verdict" {
  return row.value_kind ?? "money";
}

/** Green for made, red for missed, and for the margin either side of budget. */
function cellTone(row: GridRow, c: Cell): string {
  if (c.status !== "actual" || c.value === null) return "";
  if (row.value_kind === "verdict") {
    return Number(c.value) >= 1 ? " cell-yes" : " cell-no";
  }
  if (row.label.startsWith("% Above")) {
    return Number(c.value) >= 0 ? " cell-good" : " cell-bad";
  }
  return Number(c.value) < 0 ? " negative" : "";
}

function rowClass(row: GridRow): string {
  return `grid-row grid-${row.kind}`;
}

export default function ManagerDetail() {
  const [manager, setManager] = useState("Sam Stewart");
  const { years, currentFy } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy ?? new Date().getFullYear();
  const setFy = setFyPick;
  const [view, setView] = useState<"month" | "quarter">("month");

  const qc = useQueryClient();
  const [growth, setGrowth] = useState("");
  const [reason, setReason] = useState("");
  const [scope, setScope] = useState<"manager" | "month">("manager");
  const [month, setMonth] = useState("");
  const ref = useQuery({ queryKey: ["reference"], queryFn: api.reference });
  const yoy = useQuery({
    queryKey: ["yoy-mgr", manager, fy],
    queryFn: () => api.yearOverYear(fy, manager),
  });
  const refresh = () => { qc.invalidateQueries(); setReason(""); };

  const saveGrowth = useMutation({
    mutationFn: () => api.post("/api/budget/growth-rate",
      scope === "month"
        ? {
            scope: "manager_month", canonical_manager: manager,
            target_month: month, financial_year: null, financial_quarter: null,
            // Entered as a percentage; stored as a rate.
            growth_pct: Number(growth) / 100, dollar_override: null, reason,
          }
        : {
            scope: "manager", canonical_manager: manager, financial_year: fy,
            growth_pct: Number(growth) / 100, dollar_override: null, reason,
          }),
    onSuccess: refresh,
  });

  const lock = useMutation({
    mutationFn: (body: { target_month: string; unlock: boolean }) =>
      api.post(`/api/budget/${body.unlock ? "unlock" : "lock"}`, {
        canonical_manager: manager, target_month: body.target_month, reason,
      }),
    onSuccess: refresh,
  });

  const budget = useQuery({ queryKey: ["budget", fy], queryFn: () => api.budget(fy) });
  const monthlyBudget: any[] = (budget.data?.monthly ?? [])
    .filter((r: any) => r.canonical_manager === manager);
  const q = useQuery({
    queryKey: ["manager-detail", manager, fy],
    queryFn: () => api.managerDetail(manager, fy),
  });

  if (q.isLoading) return <Loading what="manager detail" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  const params = new URLSearchParams({ manager, financial_year: String(fy) });

  const picker = (
    <div className="controls">
      <label>
        Account manager
        <select value={manager} onChange={(e) => setManager(e.target.value)}>
          {(ref.data?.managers ?? []).map((m: any) => (
            <option key={m.canonical_manager} value={m.canonical_manager}>
              {m.canonical_manager}
              {m.include_in_rankings ? "" : " (not ranked)"}
            </option>
          ))}
        </select>
      </label>
      <label>
        Financial year
        <select value={fy} onChange={(e) => setFy(Number(e.target.value))}>
          <YearOptions years={years} />
        </select>
      </label>
      <div className="segmented">
        <button className={view === "month" ? "on" : ""} onClick={() => setView("month")}>
          Monthly
        </button>
        <button className={view === "quarter" ? "on" : ""} onClick={() => setView("quarter")}>
          Quarterly
        </button>
      </div>
      <a className="button" href={api.exportUrl("transactions", "xlsx", params)}>
        Export transactions
      </a>
    </div>
  );

  return (
    <>
      <h1>
        {d.canonical_manager}
        <span className="fy">{d.financial_year_label}</span>
        {!d.include_in_rankings && <span className="chip">not ranked</span>}
      </h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />

      {yoy.data && (
        <div className={`verdict-bar ${yoy.data.on_track === null ? "neutral"
                        : yoy.data.on_track ? "good" : "bad"}`}>
          <strong>{d.canonical_manager}: {yoy.data.verdict}</strong>
        </div>
      )}

      <Panel title="Where this manager stands"
             subtitle={`Year to date is measured to the reporting cut-off, ${d.cut_off_month}. Months after that have not started.`}
             actions={picker}>
        <div className="metric-grid">
          <Metric label="Year-to-date Actual" m={d.ytd_actual} emphasis />
          <Metric label="Year-to-date Budget" m={d.ytd_budget}
                  ratio={d.ytd_achievement}
                  hint="Budget for the months completed so far, not the full year." />
          <Metric label="Full-year Budget" m={d.full_year_budget}
                  hint="Original Renewal Forecast plus the new business growth target." />
          <Metric label="Latest Outlook" m={d.latest_outlook}
                  hint="Actuals for completed months plus Latest Forecast for the rest. No assumed new business." />
          <Metric label="Remaining Budget Gap" m={d.remaining_budget_gap}
                  hint="Still to be found through new business, retention or other actual activity." />
          <Metric label="Prior Year Actual" m={d.prior_year_actual}
                  hint="Full prior financial year, for comparison only. The budget is not derived from it." />
        </div>
      </Panel>

      <Panel title="Against budget"
             subtitle="The verdict is measured on this manager's own growth percentage, not a business-wide average.">
        <BudgetGauge
          actual={d.ytd_actual?.value != null ? Number(d.ytd_actual.value) : null}
          budget={d.ytd_budget?.value != null ? Number(d.ytd_budget.value) : null}
          label="year-to-date budget" />
        <div className="growth-current">
          <span>Growth % currently applied to {d.canonical_manager}:</span>
          <strong>
            {d.active_growth_pct?.available
              ? `${(Number(d.active_growth_pct.value) * 100).toFixed(2)}%`
              : "N/A"}
          </strong>
          <span className="chip">from {d.active_growth_basis ?? "default"}</span>
          <span className="growth-formula">
            Budget = Renewal Forecast + (Renewal Forecast &times; this %)
          </span>
        </div>
        <div className="form-row" style={{ marginTop: 12 }}>
          <label>
            Apply to
            <select value={scope} onChange={(e) => setScope(e.target.value as any)}>
              <option value="manager">Whole year</option>
              <option value="month">One month</option>
            </select>
          </label>
          {scope === "month" && (
            <label>
              Month
              <select value={month} onChange={(e) => setMonth(e.target.value)}>
                <option value="">Choose…</option>
                {d.months.map((m) => (
                  <option key={m} value={m}>{monthAU(m)}</option>
                ))}
              </select>
            </label>
          )}
          <label>
            New growth %
            <input value={growth} onChange={(e) => setGrowth(e.target.value)}
                   placeholder="e.g. 7.5" />
          </label>
          <label className="grow">
            Reason (required)
            <input value={reason} onChange={(e) => setReason(e.target.value)}
                   placeholder="why this manager's target is changing" />
          </label>
          <button disabled={!growth || reason.length < 3 || saveGrowth.isPending
                            || (scope === "month" && !month)}
                  onClick={() => saveGrowth.mutate()}>
            {saveGrowth.isPending ? "Saving…" : "Set growth %"}
          </button>
        </div>

        <div className="lock-grid">
          <div className="lock-head">
            Lock a month to freeze its budget at the figure it holds now. A locked
            month stops moving even if the forecast beneath it changes, which is
            what makes a target safe to agree with a manager.
          </div>
          {monthlyBudget.map((r: any) => (
            <div key={r.forecast_month}
                 className={`lock-cell${r.is_locked ? " locked" : ""}`}>
              <span className="lock-month">{monthAU(r.forecast_month)}</span>
              <span className="lock-budget">
                {money({ value: r.total_budget, available: true })}
              </span>
              {r.growth_basis === "manager_month" && (
                <span className="chip">month rate</span>
              )}
              <button disabled={reason.length < 3 || lock.isPending}
                      title={reason.length < 3 ? "Enter a reason first" : undefined}
                      onClick={() => lock.mutate({ target_month: r.forecast_month,
                                                   unlock: r.is_locked })}>
                {r.is_locked ? "Unlock" : "Lock"}
              </button>
              {r.is_locked && (
                <span className="lock-meta" title={r.lock_reason ?? ""}>
                  locked by {r.locked_by}
                </span>
              )}
            </div>
          ))}
        </div>
        {lock.isError && <Failed error={lock.error} />}
        <p className="footnote">
          This sets the growth percentage for {d.canonical_manager} across{" "}
          {d.financial_year_label}, overriding the global default. It changes the
          Budget only. The Original Renewal Forecast is frozen and is never
          affected. Every change is recorded with your name, the reason and the
          previous value.
        </p>
        {saveGrowth.isError && <Failed error={saveGrowth.error} />}
      </Panel>

      {yoy.data && (
        <Panel title="Month by month against budget"
               subtitle="Bars are actual against budget; the line is the same month last year.">
          <MonthlyBars data={yoy.data.months.map((m: any) => ({
            label: m.label,
            actual: m.net_actual != null ? Number(m.net_actual) : null,
            budget: m.budget != null ? Number(m.budget) : null,
            prior: m.prior_year_actual != null ? Number(m.prior_year_actual) : null,
            started: m.started,
          }))} />
        </Panel>
      )}

      {view === "quarter" ? (
        <Panel title="By quarter"
               subtitle="A quarter that has not started shows no actual figures rather than zero.">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Quarter</th>
                  <th className="right">Original Forecast</th>
                  <th className="right">Total Budget</th>
                  <th className="right">Net Actual</th>
                  <th className="right">Variance</th>
                  <th className="right">Achievement</th>
                </tr>
              </thead>
              <tbody>
                {d.quarters.map((qr: any) => (
                  <tr key={qr.quarter}>
                    <td>
                      Q{qr.quarter}
                      {!qr.started && <span className="chip">not started</span>}
                    </td>
                    <td className="right">
                      {money({ value: qr.original_forecast, available: qr.original_forecast !== null })}
                    </td>
                    <td className="right">
                      {money({ value: qr.total_budget, available: qr.total_budget !== null })}
                    </td>
                    <td className="right">
                      {qr.started
                        ? money({ value: qr.net_actual_income, available: qr.net_actual_income !== null })
                        : <span className="not-yet" title="This quarter has not started yet.">—</span>}
                    </td>
                    <td className="right">
                      {qr.variance !== null
                        ? money({ value: qr.variance, available: true })
                        : <span className="not-yet" title="This quarter has not started yet.">—</span>}
                    </td>
                    <td className="right">
                      {qr.achievement !== null
                        ? percent({ value: qr.achievement, available: true })
                        : <span className="not-yet" title="This quarter has not started yet.">—</span>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>
      ) : (
        <Panel title="Month by month"
               subtitle="Transaction types, then the forecast and budget they are measured against. An em dash means the month has not started; N/A means the measure is unavailable and the tooltip says why.">
          <div className="table-wrap">
            <table className="grid">
              <thead>
                <tr>
                  <th className="sticky-col">Transaction type / measure</th>
                  {d.months.map((m, i) => (
                    <th key={m} className={`right${d.month_status[i] === "future" ? " future-col" : ""}`}>
                      {monthAU(m)}
                    </th>
                  ))}
                  <th className="right total-col">Total</th>
                </tr>
              </thead>
              <tbody>
                {d.rows.map((row) => {
                  const k = kindOf(row);
                  return (
                    <tr key={row.label} className={rowClass(row)}>
                      <td className="sticky-col">
                        {row.label}
                        {row.hint && <span className="hint" title={row.hint}>i</span>}
                      </td>
                      {row.cells.map((c: Cell, i: number) => (
                        <td key={c.month}
                            className={`right${d.month_status[i] === "future" ? " future-col" : ""}${
                              c.status === "future" ? " not-yet" : ""}${
                              c.status === "unavailable" ? " na-cell" : ""}${
                              cellTone(row, c)}`}
                            title={cellTitle(c)}>
                          {cell(c, k)}
                        </td>
                      ))}
                      <td className="right total-col">
                        {row.total === null
                          ? (k === "percent" || k === "verdict" ? "" : "—")
                          : k === "count"
                          ? String(Math.round(Number(row.total)))
                          : k === "percent"
                          ? percent({ value: row.total, available: true })
                          : money({ value: row.total, available: true })}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Panel>
      )}
    </>
  );
}
