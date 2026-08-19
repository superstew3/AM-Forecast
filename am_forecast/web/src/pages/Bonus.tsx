import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, percent } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { DataTable, Failed, GstBanner, Loading, Metric, Panel } from "../components/ui";

/* The bonus tracker, rebuilt around one question: what is happening in the
 * quarter we are actually in.
 *
 * The previous version showed all four quarters at once. Three had not started,
 * so 42 of its 56 rows were N/A and the one quarter that mattered was buried
 * among them. Worse, it compared income against the WHOLE quarter's target one
 * month into three, which reported every manager as catastrophically behind --
 * on the live book, three managers who were AHEAD of pace read as 43% to 57%
 * under. That is arithmetic, not performance, and it is the exact fault the
 * reporting rules warn about.
 *
 * So: one quarter at a time, defaulting to the live one; income judged against
 * the budget for the months elapsed; and the year-wide table replaced by a
 * per-manager drill-down that reaches months, which is where a quarter is won
 * or lost.
 */

const TONE: Record<string, string> = {
  "ahead": "good", "on pace": "good", "bonus earned": "good",
  "behind": "warn", "well behind": "bad", "no bonus": "bad",
  "not started": "none", "in progress": "none",
};

function Pill({ status }: { status: string }) {
  return <span className={`pill pill-${TONE[status] ?? "none"}`}>{status}</span>;
}

export default function Bonus() {
  const { years, defaultYear } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? defaultYear;
  const [quarterPick, setQuarterPick] = useState<number | null>(null);
  const [manager, setManager] = useState<string | null>(null);

  const all = useQuery({
    queryKey: ["bonus", fy],
    queryFn: () => api(`/api/bonus?financial_year=${fy}&include_non_ranked=true`),
    enabled: fy != null,
  });
  const detail = useQuery({
    queryKey: ["bonus-manager", fy, manager],
    queryFn: () => api(`/api/bonus/${encodeURIComponent(manager!)}?financial_year=${fy}`),
    enabled: fy != null && manager != null,
  });

  // The live quarter is the last one that has begun. Opening on a quarter that
  // has not started shows a page of blanks; opening on Q1 in April shows
  // history. Neither is what somebody coming to this page wants.
  const liveQuarter = useMemo(() => {
    const qs = (all.data?.quarters ?? []).filter((q: any) => q.quarter_started)
      .map((q: any) => q.financial_quarter);
    return qs.length ? Math.max(...qs) : 1;
  }, [all.data]);

  if (all.isLoading) return <Loading what="the bonus tracker" />;
  if (all.isError) return <Failed error={all.error} retry={() => all.refetch()} />;
  const d = all.data!;

  const quarter = quarterPick ?? liveQuarter;
  const rows = d.quarters.filter((q: any) => q.financial_quarter === quarter);
  const label = rows[0]?.quarter_label ?? `Q${quarter}`;
  const elapsed = rows[0]?.months_elapsed ?? 0;
  const inQuarter = rows[0]?.months_in_quarter ?? 3;
  const started = rows.some((r: any) => r.quarter_started);
  const sum = (k: string) => rows.reduce((t: number, r: any) => t + Number(r[k] ?? 0), 0);
  const ahead = rows.filter((r: any) => r.pace === "ahead").length;
  const rated = rows.filter((r: any) => r.pace).length;

  return (
    <>
      <h1>
        Bonus tracker
        <span className="fy">{d.financial_year_label}</span>
        <select className="inline-select" value={fy}
                onChange={(e) => { setFyPick(Number(e.target.value)); setQuarterPick(null); }}>
          <YearOptions years={years} />
        </select>
      </h1>
      <GstBanner meta={d.meta} />

      <div className="quarter-bar">
        <div className="quarter-tabs">
          {[1, 2, 3, 4].map((q) => {
            const qr = d.quarters.find((x: any) => x.financial_quarter === q);
            return (
              <button key={q} onClick={() => setQuarterPick(q)}
                      className={`quarter-tab${q === quarter ? " is-active" : ""}`}>
                {qr?.quarter_label ?? `Q${q}`}
                {q === liveQuarter && <span className="live-dot">now</span>}
              </button>
            );
          })}
        </div>
        <div className="quarter-progress">
          {started ? `${elapsed} of ${inQuarter} months complete` : "Not started"}
        </div>
      </div>

      {/* Four cards, equal weight, all describing the same period. The old
          header mixed a headcount, a full-year figure and two to-date figures at
          identical size, so nothing said which one was the answer. */}
      <Panel title={`${label} — what is payable`} subtitle={d.scheme.gst_note}>
        <div className="metric-grid metric-grid-4">
          <Metric label="Earned so far" emphasis
                  m={{ value: sum("total_bonus"), available: started }}
                  hint="Payable if the quarter closed today. Normally nil part-way through, because the target is judged over the whole quarter." />
          <Metric label="Projected at this pace"
                  m={{ value: sum("projected_bonus"), available: started }}
                  hint="If the pace of the completed months continues. Not money earned." />
          <Metric label="If every target is met"
                  m={{ value: sum("bonus_at_target"), available: true }}
                  hint="Base bonus only, assuming each target is hit exactly and nothing earned above it." />
          <Metric label="Ahead of pace" kind="count"
                  m={{ value: rated ? `${ahead} of ${rated}` : null, available: rated > 0 }}
                  hint="Managers at or above the budget for the months elapsed." />
        </div>
      </Panel>

      <Panel title={`${label} — by manager`}
             subtitle="Income is compared with the budget for the months elapsed, not the whole quarter. Choose a manager for their month-by-month position.">
        <DataTable
          caption="managers"
          rows={rows}
          onRowClick={(r: any) => setManager(r.canonical_manager)}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "actual_income", label: "Income to date", align: "right",
              render: (r: any) => money({ value: r.actual_income, available: true }) },
            { key: "budget_to_date", label: "Target to date", align: "right",
              hint: "The budget for the months that have elapsed, not the whole quarter.",
              render: (r: any) => money({ value: r.budget_to_date, available: r.quarter_started }) },
            { key: "pace_variance", label: "Ahead / (behind)", align: "right",
              hint: "Income less the target for the months elapsed.",
              render: (r: any) => money({ value: r.pace_variance, available: r.pace_variance != null }) },
            { key: "pace_achievement", label: "Pace", align: "right",
              render: (r: any) => percent({ value: r.pace_achievement, available: r.pace_achievement != null }) },
            { key: "budget_target", label: "Whole quarter", align: "right",
              render: (r: any) => money({ value: r.budget_target, available: true }) },
            { key: "income_still_required", label: "Still needed", align: "right",
              hint: "Income required over the rest of the quarter to reach the target.",
              render: (r: any) => money({ value: r.income_still_required, available: r.quarter_started }) },
            { key: "total_bonus", label: "Bonus if it closed today", align: "right",
              render: (r: any) => money({ value: r.total_bonus, available: r.quarter_started }) },
            { key: "status", label: "Standing",
              render: (r: any) => <Pill status={r.status} /> },
          ]}
        />
      </Panel>

      {/* A picker as well as a row click: comparing two managers previously meant
          scrolling back up and hunting for the right row. */}
      <Panel title="One manager, month by month"
             subtitle="A bonus is a quarterly entitlement, so the monthly figures do not sum to it. They show where a quarter was won or lost.">
        <div className="manager-picker">
          {d.managers.map((m: any) => (
            <button key={m.canonical_manager}
                    className={`chip${manager === m.canonical_manager ? " is-active" : ""}`}
                    onClick={() => setManager(manager === m.canonical_manager
                                              ? null : m.canonical_manager)}>
              {m.canonical_manager}
            </button>
          ))}
        </div>
        {!manager && <p className="empty">Choose a manager above.</p>}
        {manager && detail.isLoading && <Loading what={manager} />}
        {manager && detail.data && (
          <ManagerMonths d={detail.data} quarter={quarter} label={label} />
        )}
      </Panel>
    </>
  );
}

function ManagerMonths({ d, quarter, label }: { d: any; quarter: number; label: string }) {
  const months = d.months.filter((m: any) => m.financial_quarter === quarter);
  const q = d.quarters.find((x: any) => x.financial_quarter === quarter);
  if (!q) return <p className="empty">No figures for {label}.</p>;
  const name = (iso: string) =>
    new Date(iso).toLocaleDateString("en-AU", { month: "long", year: "numeric" });

  return (
    <div className="manager-months">
      <div className="months-head">
        <h3>{d.canonical_manager} — {label}</h3>
        <Pill status={q.status} />
      </div>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Month</th>
              <th className="right">Target</th>
              <th className="right">Income</th>
              <th className="right">Ahead / (behind)</th>
              <th className="right">Indicative bonus</th>
            </tr>
          </thead>
          <tbody>
            {months.map((m: any) => {
              const variance = m.month_started
                ? Number(m.actual_income ?? 0) - Number(m.budget_target ?? 0) : null;
              return (
                <tr key={m.period_month} className={m.month_started ? "" : "row-future"}>
                  <td>{name(m.period_month)}</td>
                  <td className="right">{money({ value: m.budget_target, available: true })}</td>
                  <td className="right">
                    {m.month_started
                      ? money({ value: m.actual_income ?? 0, available: true })
                      : <span className="na">not started</span>}
                  </td>
                  <td className="right">
                    {variance === null ? <span className="na">—</span>
                      : money({ value: variance, available: true })}
                  </td>
                  <td className="right">
                    {m.month_started
                      ? money({ value: m.indicative_bonus ?? 0, available: true })
                      : <span className="na">—</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
          <tfoot>
            {/* The quarter is the entitlement. The monthly column above is
                indicative and deliberately does not add up to it: a quarter can
                be missed overall while months inside it ran ahead. */}
            <tr>
              <th>{label} — the entitlement</th>
              <td className="right">{money({ value: q.budget_target, available: true })}</td>
              <td className="right">{money({ value: q.actual_income, available: true })}</td>
              <td className="right">
                {q.pace_variance == null ? <span className="na">—</span>
                  : money({ value: q.pace_variance, available: true })}
              </td>
              <td className="right total-bonus">
                {money({ value: q.total_bonus ?? 0, available: q.quarter_started })}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>
      <p className="months-note">
        Monthly figures are indicative. The entitlement is quarterly, and a month
        showing a bonus does not create one until the quarter closes above its
        target.
        {q.income_still_required != null && Number(q.income_still_required) > 0 && (
          <> {d.canonical_manager} needs{" "}
            <strong>{money({ value: q.income_still_required, available: true })}</strong>
            {" "}more this quarter to reach the target.</>
        )}
      </p>
    </div>
  );
}
