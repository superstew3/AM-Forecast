import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { DataTable, Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";

/* Performance — the two-ledger model, on a screen at last.
 *
 * Migration 0018 built this and nothing read it, so every rule agreed for the
 * model was invisible. This page exists to make each one visible:
 *
 *   A month still running is NEVER scored. It shows month-to-date income beside
 *   its target and says "Month in progress", because a whole month's target
 *   against a part month's income is not a result.
 *
 *   A month that began with no target says Missing Forecast, rather than showing
 *   nothing and letting the reader assume zero.
 *
 *   A month whose transactions were never imported says so, rather than showing
 *   $0.00 and 0% -- which reads identically to a month where nobody earned
 *   anything, and is the difference between "we did badly" and "nobody uploaded
 *   the file".
 *
 * Every one of those sentences comes from the database, not from this file. The
 * views carry the wording so that what the reader sees and what the figures mean
 * cannot drift apart.
 */

const TONE: Record<string, string> = {
  achieved: "good",
  below_target: "bad",
  in_progress: "none",
  not_started: "none",
  missing_forecast: "warn",
  actuals_not_loaded: "warn",
  actuals_partial: "warn",
  baseline_unverified: "warn",
};

const WORDS: Record<string, string> = {
  achieved: "Achieved",
  below_target: "Below target",
  in_progress: "Month in progress",
  not_started: "Not started",
  missing_forecast: "Missing forecast",
  actuals_not_loaded: "Actuals not loaded",
  actuals_partial: "Actuals part loaded",
  baseline_unverified: "Baseline unverified",
};

function Status({ s, note }: { s: string; note?: string | null }) {
  return (
    <span className={`pill pill-${TONE[s] ?? "none"}`} title={note ?? undefined}>
      {WORDS[s] ?? s}
    </span>
  );
}

export default function Performance() {
  const { years, currentFy } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy;
  const [manager, setManager] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["performance", fy],
    queryFn: () => api.performance(fy!),
    enabled: fy != null,
  });
  const byMonth = useQuery({
    queryKey: ["performance-months", fy],
    queryFn: () => api.performanceMonths(fy!),
    enabled: fy != null,
  });

  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  if (fy == null || !q.data) return <Loading what="performance" />;
  const d = q.data;

  const managers = [...new Set(d.months.map((m: any) => m.canonical_manager))].sort();
  const rows = manager ? d.months.filter((m: any) => m.canonical_manager === manager)
                       : (byMonth.data?.months ?? []);

  const current = (byMonth.data?.months ?? [])
    .find((m: any) => m.month === d.current_month);

  return (
    <>
      <h1>
        Performance
        <span className="fy">FY{fy}-{String(fy + 1).slice(2)}</span>
        <select className="inline-select" value={fy}
                onChange={(e) => setFyPick(Number(e.target.value))}>
          <YearOptions years={years} />
        </select>
      </h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />

      {/* The month under way, said plainly. It is the thing somebody opening this
          page wants, and it is the month most easily misread -- so the note
          explaining how far the actuals run sits directly beneath the figure. */}
      {current && (
        <Panel title={`${monthAU(current.month)} — the month under way`}>
          <div className="metric-grid metric-grid-4">
            <Metric label="Actual income, month to date" emphasis
                    m={{ value: current.actual_income,
                         available: current.actual_income != null }}
                    hint={current.status_note ?? undefined} />
            <Metric label="Target for the month"
                    m={{ value: current.target_income, available: true }} />
            <Metric label="Forecast before growth"
                    m={{ value: current.forecast_income, available: true }} />
            <Metric label="Managers" kind="count"
                    m={{ value: current.managers, available: true }} />
          </div>
          <p className="months-note">
            {current.status_note ??
              "A month still running is not scored: achievement is withheld until it closes."}
          </p>
        </Panel>
      )}

      {/* Anything the reader needs to know before trusting a figure, gathered in
          one place rather than left to be discovered a row at a time. */}
      {(d.missing_forecast.length > 0 || d.baseline_basis.length > 0 ||
        d.coverage.some((c: any) => c.load_state !== "full")) && (
        <Panel title="Worth knowing before reading the figures">
          <ul className="advisories">
            {d.missing_forecast.map((m: any) => (
              <li key={`mf-${m.month}`} className="advisory advisory-warn">
                <strong>{monthAU(m.month)}</strong> began with no target.
                A routine upload cannot fill it — that needs an audited override.
                {m.override_pending && " An override is already open for it."}
              </li>
            ))}
            {d.coverage.filter((c: any) => c.load_state !== "full").map((c: any) => (
              <li key={`cv-${c.month}`} className="advisory">
                <strong>{monthAU(c.month)}</strong>{" "}
                {c.load_state === "none"
                  ? "has no transactions imported, so its actuals are unavailable rather than nil."
                  : <>has transactions to {monthAU(c.loaded_to)} only, so it is not
                     scored as a completed month.</>}
              </li>
            ))}
            {d.baseline_basis.map((b: any) => (
              <li key={`bb-${b.month}`} className="advisory advisory-warn">
                <strong>{monthAU(b.month)}</strong> rests on {b.rows_unverified} baseline
                rows still on the old gross basis
                ({money({ value: b.value_unverified, available: true })}).
                Excluded from achievement and bonus until reconstructed.
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <div className="manager-picker">
        <button className={`chip${manager === null ? " is-active" : ""}`}
                onClick={() => setManager(null)}>Whole business</button>
        {managers.map((m: any) => (
          <button key={m} className={`chip${manager === m ? " is-active" : ""}`}
                  onClick={() => setManager(manager === m ? null : m)}>{m}</button>
        ))}
      </div>

      <Panel title={manager ? `${manager} — month by month` : "The business, month by month"}
             subtitle="Expected and actual side by side for every month. Neither ledger ever replaces the other.">
        <DataTable
          caption="months"
          rows={rows}
          rowKey={(r: any) => `${r.canonical_manager ?? "all"}-${r.month}`}
          columns={[
            { key: "month", label: "Month", render: (r: any) => monthAU(r.month) },
            { key: "forecast_income", label: "Forecast", align: "right",
              render: (r: any) => money({ value: r.forecast_income, available: true }) },
            { key: "target_income", label: "Target", align: "right",
              hint: "Forecast plus the growth goal.",
              render: (r: any) => money({ value: r.target_income, available: true }) },
            { key: "actual_income", label: "Actual", align: "right",
              hint: "Month-to-date for the month under way; final once it closes.",
              render: (r: any) => (r.actual_income == null
                ? <span className="na">N/A</span>
                : money({ value: r.actual_income, available: true })) },
            { key: "variance", label: "Variance", align: "right",
              render: (r: any) => (r.variance == null
                ? <span className="na">—</span>
                : money({ value: r.variance, available: true })) },
            { key: "achievement_pct", label: "Achievement", align: "right",
              hint: "Withheld for a month not started, one still running, and one whose transactions are not in.",
              render: (r: any) => (r.achievement_pct == null
                ? <span className="na">—</span>
                : `${Number(r.achievement_pct).toFixed(1)}%`) },
            { key: "status", label: "Standing",
              render: (r: any) => (r.status
                ? <Status s={r.status} note={r.status_note} />
                : <span className="na">—</span>) },
          ]}
        />
      </Panel>

      {manager && (
        <Panel title={`${manager} — by quarter`}
               subtitle="Achievement counts only months that have closed, so a quarter part-way through is not scored against its whole target.">
          <DataTable
            caption="quarters"
            rows={d.quarters.filter((x: any) => x.canonical_manager === manager)}
            rowKey={(r: any) => `${r.canonical_manager}-${r.financial_quarter}`}
            columns={[
              { key: "financial_quarter", label: "Quarter",
                render: (r: any) => `Q${r.financial_quarter}` },
              { key: "forecast_income", label: "Forecast", align: "right",
                render: (r: any) => money({ value: r.forecast_income, available: true }) },
              { key: "target_income", label: "Target", align: "right",
                render: (r: any) => money({ value: r.target_income, available: true }) },
              { key: "actual_income", label: "Actual", align: "right",
                render: (r: any) => money({ value: r.actual_income, available: true }) },
              { key: "latest_outlook", label: "Outlook", align: "right",
                hint: "Actual for months that have closed, expected for the rest.",
                render: (r: any) => money({ value: r.latest_outlook, available: true }) },
              { key: "achievement_pct_completed", label: "Achievement", align: "right",
                render: (r: any) => (r.achievement_pct_completed == null
                  ? <span className="na">—</span>
                  : `${Number(r.achievement_pct_completed).toFixed(1)}%`) },
              { key: "months_in_progress", label: "", align: "right",
                render: (r: any) => (Number(r.months_in_progress) > 0
                  ? <span className="pill pill-none">{r.months_in_progress} in progress</span>
                  : null) },
            ]}
          />
        </Panel>
      )}
    </>
  );
}
