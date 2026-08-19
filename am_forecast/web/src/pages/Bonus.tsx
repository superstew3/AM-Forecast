import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, money, percent } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { BudgetGauge, ChangeBars, MonthlyBars } from "../components/charts";
import { DataTable, Failed, GstBanner, Loading, Metric, Notes, Panel, Value } from "../components/ui";

const STATUS_LABEL: Record<string, string> = {
  earned: "Earned", missed: "Missed", "on track": "On track",
  behind: "Behind", "not started": "Not started",
};

/**
 * Bonus Tracker.
 *
 * Two figures are kept apart throughout: what has been earned, and what is
 * projected at the current pace. Only the first is money.
 */
export default function Bonus() {
  const { years, currentFy } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy ?? new Date().getFullYear();
  const [manager, setManager] = useState<string | null>(null);

  const all = useQuery({ queryKey: ["bonus", fy], queryFn: () => api.bonus(fy) });
  const one = useQuery({
    queryKey: ["bonus-manager", manager, fy],
    queryFn: () => api.bonusForManager(manager!, fy),
    enabled: !!manager,
  });

  if (all.isLoading) return <Loading what="the bonus tracker" />;
  if (all.isError) return <Failed error={all.error} retry={() => all.refetch()} />;
  const d = all.data!;

  const num = (v: any) => (v === null || v === undefined ? null : Number(v));

  return (
    <>
      <h1>
        Bonus tracker
        <span className="fy">{d.financial_year_label}</span>
        <select className="inline-select" value={fy}
                onChange={(e) => setFyPick(Number(e.target.value))}>
          <YearOptions years={years} />
        </select>
      </h1>
      <GstBanner meta={d.meta} />

      <div className="purpose">
        <strong>How the bonus works.</strong> {d.scheme.description}
        <ul className="formula">
          {d.scheme.formula.map((f: string) => <li key={f}><code>{f}</code></li>)}
        </ul>
        <em>Earned is not projected.</em> A quarter still running shows the bonus
        that would pay if it closed today — usually nil part-way through — with the
        projection at current pace reported separately. Projections are not money.
      </div>

      {/* Three figures, not seven.
          The seven-metric row mixed money with a headcount, earned with
          projected, and to-date with full-year, all at the same size and weight
          -- so the eye could not tell which one answered "what has been earned".
          Two of them (total actual income, total budget target) belong to the
          business page and were repeated here.

          What is left is the question the page exists to answer -- what is
          payable now -- with the two forward-looking figures beside it, clearly
          separated and clearly labelled as not money. */}
      <Panel title="What is payable"
             subtitle={d.scheme.gst_note}>
        <div className="metric-grid metric-grid-3">
          <Metric label="Earned to date" emphasis
                  m={{ value: d.totals.earned_bonus, available: true }}
                  hint={d.column_scope.earned_bonus} />
          <Metric label="Projected — quarters under way"
                  m={{ value: d.totals.projected_bonus, available: true }}
                  hint={d.column_scope.projected_bonus} />
          <Metric label="Full year at target"
                  m={{ value: d.totals.bonus_at_target, available: true }}
                  hint={d.column_scope.bonus_at_target} />
        </div>
        <div className="metric-footnote">
          {d.totals.managers} managers in the scheme.
          {" "}Full-year outlook <Value m={{ value: d.totals.full_year_outlook,
                                             available: true }} />.
          {" "}Income and targets are on the{" "}
          <a href="/business">business page</a>.
        </div>
      </Panel>

      <Panel title="Bonus by manager"
             subtitle="The three bonus columns cover different periods and are not comparable with each other. Hover any heading for its exact scope. Click a manager for their quarter-by-quarter position.">
        <div className="scope-key">
          <span><strong>Earned to date</strong> — {d.column_scope.earned_bonus}</span>
          <span><strong>Projected</strong> — {d.column_scope.projected_bonus}</span>
          <span><strong>Full year at target</strong> — {d.column_scope.bonus_at_target}</span>
        </div>
        <DataTable
          caption="managers"
          rows={d.managers}
          onRowClick={(r: any) => setManager(r.canonical_manager)}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "ytd_actual", label: "Actual (started quarters)", align: "right",
              render: (r: any) => money({ value: r.ytd_actual, available: true }) },
            { key: "ytd_budget_target", label: "Target", align: "right",
              render: (r: any) => money({ value: r.ytd_budget_target, available: true }) },
            { key: "gap", label: "Above / (below)", align: "right",
              render: (r: any) => money({
                value: Number(r.ytd_actual) - Number(r.ytd_budget_target),
                available: true }) },
            { key: "quarters_started", label: "Quarters started", align: "right",
              hint: "How many of the year's four quarters have begun. The Earned and Projected columns cover only these.",
              render: (r: any) => `${r.quarters_started} of ${r.quarters_total}` },
            { key: "earned_bonus", label: "Earned to date", align: "right",
              hint: d.column_scope.earned_bonus,
              render: (r: any) => money({ value: r.earned_bonus, available: true }) },
            { key: "projected_bonus", label: "Projected (started quarters)",
              align: "right", hint: d.column_scope.projected_bonus,
              render: (r: any) => money({ value: r.projected_bonus, available: true }) },
            { key: "bonus_at_target", label: "Full year at target", align: "right",
              hint: d.column_scope.bonus_at_target,
              render: (r: any) => money({ value: r.bonus_at_target, available: true }) },
            { key: "full_year_outlook", label: "Full year outlook", align: "right",
              hint: d.column_scope.full_year_outlook,
              render: (r: any) => money({ value: r.full_year_outlook, available: true }) },
          ]}
        />
      </Panel>

      <div className="two-col">
        <Panel title="Projected bonus, quarters under way"
               subtitle="At the pace of the months completed. Quarters not yet begun are excluded.">
          <ChangeBars items={d.managers.map((m: any) => ({
            label: m.canonical_manager, change: Number(m.projected_bonus),
          }))} limit={15} />
        </Panel>
        <Panel title="Distance to target"
               subtitle="Actual income less budget target, for quarters that have started.">
          <ChangeBars items={d.managers.map((m: any) => ({
            label: m.canonical_manager,
            change: Number(m.ytd_actual) - Number(m.ytd_budget_target),
          }))} limit={15} />
        </Panel>
      </div>

      <Panel title="Every quarter"
             subtitle="A quarter that has not started carries no bonus figure at all, rather than a nil.">
        <DataTable
          caption="quarters"
          rows={d.quarters}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "quarter_label", label: "Quarter" },
            { key: "months_elapsed", label: "Months",
              render: (r: any) => `${r.months_elapsed}/${r.months_in_quarter}` },
            { key: "expected_income", label: "Expected income", align: "right",
              render: (r: any) => money({ value: r.expected_income, available: true }) },
            { key: "growth_pct", label: "Growth %", align: "right",
              render: (r: any) => percent({ value: r.growth_pct,
                                            available: r.growth_pct !== null }) },
            { key: "growth_target_amount", label: "Growth target", align: "right",
              render: (r: any) => money({ value: r.growth_target_amount, available: true }) },
            { key: "budget_target", label: "Budget target", align: "right",
              render: (r: any) => money({ value: r.budget_target, available: true }) },
            { key: "actual_income", label: "Actual", align: "right",
              render: (r: any) => money({ value: r.actual_income,
                                          available: r.quarter_started }) },
            { key: "above_below_target", label: "Above / (below)", align: "right",
              render: (r: any) => money({ value: r.above_below_target,
                                          available: r.quarter_started }) },
            { key: "income_still_required", label: "Still needed", align: "right",
              hint: "Income still to earn before any bonus is payable.",
              render: (r: any) => money({ value: r.income_still_required,
                                          available: r.quarter_started }) },
            { key: "total_bonus", label: "Bonus earned", align: "right",
              render: (r: any) => money({ value: r.total_bonus,
                                          available: r.total_bonus !== null }) },
            { key: "projected_bonus", label: "Projected", align: "right",
              render: (r: any) => money({ value: r.projected_bonus,
                                          available: r.projected_bonus !== null }) },
            { key: "status", label: "Status",
              render: (r: any) => (
                <span className={`verdict-chip status-${r.status.replace(" ", "-")}`}>
                  {STATUS_LABEL[r.status] ?? r.status}
                </span>
              ) },
          ]}
        />
      </Panel>

      {manager && one.data && (
        <Panel title={`${manager} — quarter by quarter`}
               actions={<button onClick={() => setManager(null)}>Close</button>}>
          {one.data.quarters.map((q: any) => (
            <div key={q.financial_quarter} className="bonus-quarter">
              <div className="bonus-quarter-head">
                <strong>{q.quarter_label}</strong>
                <span className={`verdict-chip status-${q.status.replace(" ", "-")}`}>
                  {STATUS_LABEL[q.status] ?? q.status}
                </span>
                <span className="bonus-months">
                  {q.months_elapsed} of {q.months_in_quarter} months complete
                </span>
              </div>
              <BudgetGauge actual={q.quarter_started ? num(q.actual_income) : null}
                           budget={num(q.budget_target)}
                           label={`${q.quarter_label} target`} />
              <div className="bonus-breakdown">
                <span>Expected {money({ value: q.expected_income, available: true })}</span>
                <span>+ growth {percent({ value: q.growth_pct,
                                          available: q.growth_pct !== null })}</span>
                <span>= target {money({ value: q.budget_target, available: true })}</span>
                <span className="sep">|</span>
                <span>Base {money({ value: q.bonus_at_target, available: true })}</span>
                <span>Above-target {money({ value: q.above_target_bonus,
                                            available: q.above_target_bonus !== null })}</span>
                <strong>Bonus {money({ value: q.total_bonus,
                                       available: q.total_bonus !== null })}</strong>
              </div>
            </div>
          ))}
          <MonthlyBars data={one.data.months.map((m: any) => ({
            label: new Date(`${m.period_month}T00:00:00`).toLocaleDateString("en-AU",
              { month: "short", year: "2-digit" }),
            actual: m.month_started && m.actual_income !== null
              ? Number(m.actual_income) : null,
            budget: m.budget_target !== null ? Number(m.budget_target) : null,
            started: m.month_started,
          }))} />
          <Notes notes={one.data.meta.notes} />
        </Panel>
      )}

      <Notes notes={d.meta.notes} />
    </>
  );
}
