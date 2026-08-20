import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { api, money, percent } from "../lib/api";
import { BudgetGauge, ChangeBars, MonthlyBars } from "../components/charts";
import { BaselineWarning, Failed, GstBanner, Loading, Metric, Notes, Panel, Value } from "../components/ui";

export default function Business() {
  const { years, currentFy } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  // Default to the current financial year once the data says what it is.
  const fy = fyPick ?? currentFy ?? new Date().getFullYear();
  const setFy = setFyPick;
  const [pick, setPick] = useState<string | null>(null);
  const [scope, setScope] = useState<"ytd" | "q1" | "q2" | "q3" | "q4" | "year">("ytd");

  const biz = useQuery({ queryKey: ["business", fy], queryFn: () => api.business(fy) });
  const yoy = useQuery({ queryKey: ["yoy", fy], queryFn: () => api.yearOverYear(fy) });

  if (biz.isLoading || yoy.isLoading) return <Loading what="business performance" />;
  if (biz.isError) return <Failed error={biz.error} retry={() => biz.refetch()} />;
  if (yoy.isError) return <Failed error={yoy.error} retry={() => yoy.refetch()} />;
  const d = biz.data!;
  const y = yoy.data!;

  const series = y.months.map((m: any) => ({
    label: m.label,
    actual: m.net_actual != null ? Number(m.net_actual) : null,
    budget: m.budget != null ? Number(m.budget) : null,
    prior: m.prior_year_actual != null ? Number(m.prior_year_actual) : null,
    started: m.started,
  }));

  const growthUp = Number(y.ytd_growth?.value ?? 0) >= 0;

  /** Which months the selected scope covers. */
  const inScope = (i: number) => {
    if (scope === "year") return true;
    if (scope === "ytd") return y.months[i].started;
    const q = Number(scope[1]);
    return i >= (q - 1) * 3 && i < q * 3;
  };
  const scopedMonths = y.months.filter((_: any, i: number) => inScope(i));
  const sum = (key: string) =>
    scopedMonths.reduce((t: number, m: any) =>
      m[key] != null ? t + Number(m[key]) : t, 0);
  const anyStarted = scopedMonths.some((m: any) => m.started);
  const scopeActual = anyStarted ? sum("net_actual") : null;
  const scopeBudget = scopedMonths.reduce((t: number, m: any) =>
    (m.budget != null && (scope === "year" || m.started)) ? t + Number(m.budget) : t, 0);
  const scopePrior = sum("prior_year_actual");
  const scopeLabel = scope === "ytd" ? "Year to date"
    : scope === "year" ? "Full year" : `Q${scope[1]} ${y.label}`;

  const SCOPES: { key: typeof scope; label: string }[] = [
    { key: "ytd", label: "Year to date" },
    { key: "q1", label: "Q1 Jul-Sep" },
    { key: "q2", label: "Q2 Oct-Dec" },
    { key: "q3", label: "Q3 Jan-Mar" },
    { key: "q4", label: "Q4 Apr-Jun" },
    { key: "year", label: "Full year" },
  ];

  return (
    <>
      <h1>
        Business performance
        <span className="fy">{y.label}</span>
        <select className="inline-select" value={fy} onChange={(e) => setFy(Number(e.target.value))}>
          <YearOptions years={years} />
        </select>
      </h1>
      <GstBanner meta={d.meta} />

      <div className={`verdict-bar ${y.on_track === null ? "neutral" : y.on_track ? "good" : "bad"}`}>
        <strong>{y.verdict}</strong>
      </div>

      <Panel title="This year against last"
             subtitle={`Like for like: ${y.prior_label} is cut at the same month of the year as the current reporting cut-off, so a part year is never compared with a full one.`}>
        <div className="metric-grid">
          <Metric label="Earned this year to date" m={y.ytd_actual} emphasis />
          <Metric label={`Same period ${y.prior_label}`} m={y.ytd_prior_year} />
          <div className={`metric metric-emphasis tone-${growthUp ? "good" : "bad"}`}>
            <div className="metric-label">Growth on prior year</div>
            <div className="metric-value">
              {growthUp ? "+" : ""}<Value m={y.ytd_growth} />
            </div>
            <div className="metric-sub">
              {growthUp ? "up " : "down "}<Value m={y.ytd_growth_pct} kind="percent" /> on the same period
            </div>
          </div>
          <Metric label={`${y.prior_label} full year`} m={y.prior_year_full}
                  hint="The whole prior year, for context. The budget is not derived from it." />
        </div>
      </Panel>

      <Panel title={`Against budget — ${scopeLabel}`}
             subtitle="Budget is the renewal forecast plus the new business growth target. Achievement is measured only on months that have started."
             actions={
               <div className="segmented">
                 {SCOPES.map((sc) => (
                   <button key={sc.key} className={scope === sc.key ? "on" : ""}
                           onClick={() => setScope(sc.key)}>{sc.label}</button>
                 ))}
               </div>
             }>
        <BudgetGauge actual={scopeActual} budget={anyStarted ? scopeBudget : null}
                     label={`${scopeLabel.toLowerCase()} budget`} />
        {/* Three figures for the period chosen, then three for the year ahead.
            Twelve at once, all the same size, meant nothing said which was the
            answer -- and two of them were repeats: "Year-to-date Budget" appeared
            in both rows, and "Variance to Budget" restated what the gauge above
            already shows in a bar and a percentage. */}
        <div className="metric-grid metric-grid-3" style={{ marginTop: 14 }}>
          <Metric label={`${scopeLabel} actual`}
                  m={{ value: scopeActual, available: scopeActual !== null,
                       reason: "This period has not started yet." }} emphasis />
          <Metric label={`${scopeLabel} budget`}
                  m={{ value: anyStarted ? scopeBudget : null, available: anyStarted,
                       reason: "This period has not started yet." }} />
          <Metric label={`${scopeLabel} last year`}
                  m={{ value: scopePrior || null, available: scopePrior !== 0,
                       reason: "No prior-year figure for this period." }} />
        </div>

        <div className="section-rule">Where the full year is heading</div>
        <div className="metric-grid metric-grid-3">
          <Metric label="Full-year budget" m={y.full_year_budget} />
          <Metric label="Latest outlook" m={y.latest_outlook}
                  hint="Actuals for completed months plus Latest Forecast for the rest. No assumed new business." />
          <Metric label="Remaining gap" m={y.remaining_gap}
                  hint="Still to be found through new business, retention or other actual activity." />
        </div>
        <p className="months-note">
          Outlook against last year's result:{" "}
          <strong>{percent(y.outlook_vs_prior_year_pct)}</strong>.
          {" "}Year-to-date budget is {money(y.ytd_budget)}, and the gauge above
          measures the selected period against it.
        </p>
      </Panel>

      <Panel title="Month by month"
             subtitle="Bars are actual against budget; the line is the same month last year. Months that have not started are left empty rather than drawn as zero.">
        <MonthlyBars data={series} onSelect={setPick} selected={pick} />
        {pick && (() => {
          const m = y.months.find((x: any) => x.label === pick);
          if (!m) return null;
          return (
            <div className="month-detail">
              <strong>{pick}</strong>
              <span>Actual <Value m={{ value: m.net_actual,
                                       available: m.net_actual !== null,
                                       reason: "This month has not started yet." }} /></span>
              <span>Budget <Value m={{ value: m.budget, available: m.budget !== null }} /></span>
              <span>Variance <Value m={{ value: m.variance_to_budget,
                                         available: m.variance_to_budget !== null,
                                         reason: "This month has not started yet." }} /></span>
              <span>Achievement <Value m={{ value: m.achievement,
                                            available: m.achievement !== null,
                                            reason: "This month has not started yet." }}
                                       kind="percent" /></span>
              <span>Last year <Value m={{ value: m.prior_year_actual,
                                          available: m.prior_year_actual !== null,
                                          reason: "No prior-year figure." }} /></span>
              <button onClick={() => setPick(null)}>Clear</button>
            </div>
          );
        })()}
      </Panel>

      <div className="two-col">
        <Panel title="Where the growth is coming from"
               subtitle="Change on the same period last year, by account manager.">
          <ChangeBars items={y.growth_by_manager.map((r: any) => ({
            label: r.canonical_manager, change: Number(r.change),
          }))} />
        </Panel>
        <Panel title="By transaction type"
               subtitle="Which kinds of business moved.">
          <ChangeBars items={y.growth_by_type.map((r: any) => ({
            label: r.classification, change: Number(r.change),
          }))} />
        </Panel>
      </div>

      <Panel title="Income and leakage">
        <div className="metric-grid">
          <Metric label="Positive Actual Income" m={d.positive_actual_income} />
          <Metric label="Return Income" m={d.return_income}
                  hint="Money that came back out. Reduces Net Actual Income." />
          <Metric label="Net Actual Income" m={d.net_actual_income} emphasis />
          <Metric label="Actual New Business" m={d.actual_new_business}
                  hint="Recognised only once it appears in Sales Transactions." />
          <Metric label="Lapse / Lost Renewal" m={d.lapse_return_income} />
          <Metric label="Mid-Term Cancellation" m={d.midterm_cancellation_return_income} />
          <Metric label="New Business Cancellation" m={d.new_business_cancellation_return_income} />
          <Metric label="Negative Endorsements" m={d.negative_endorsements} />
        </div>
      </Panel>

      <Panel title="Renewal forecast"
             subtitle="The forecast the budget is built on. Completed months keep the figure they were measured against; future months update when a newer Renewals Pending file is loaded.">
        <div className="metric-grid">
          <Metric label="Renewal Forecast" m={d.original_renewal_forecast} />
          <Metric label="Total Budget" m={d.total_budget}
                  hint="Renewal Forecast plus the new business growth target." />
          <Metric label="Latest Outlook" m={d.latest_outlook} />
        </div>
      </Panel>

      <Notes notes={d.meta.notes} />
      {fy === 2026 && (
        <BaselineWarning month="July 2026"
          source="A renewal forecast per manager was entered directly" />
      )}
    </>
  );
}
