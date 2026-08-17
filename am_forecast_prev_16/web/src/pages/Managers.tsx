import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { ManagerRow, api, monthAU } from "../lib/api";
import { NOT_YET } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel, Value } from "../components/ui";

const PERIODS = [
  { key: "month", label: "Monthly" },
  { key: "quarter", label: "Quarterly" },
  { key: "ytd", label: "Year to date" },
  { key: "year", label: "Full year" },
];

export default function Managers() {
  const [period, setPeriod] = useState("quarter");
  const { years, currentFy, label: fyLabelOf } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  // Default to the current financial year once the data says what it is.
  const fy = fyPick ?? currentFy ?? new Date().getFullYear();
  const setFy = setFyPick;
  const [includeNonRanked, setIncludeNonRanked] = useState(false);
  const params = new URLSearchParams({
    period,
    financial_year: String(fy),
    include_non_ranked: String(includeNonRanked),
  });
  const fyLabel = fyLabelOf(fy);
  const q = useQuery({
    queryKey: ["managers", period, fy, includeNonRanked],
    queryFn: () => api.managers(params),
  });

  if (q.isLoading) return <Loading what="manager performance" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  /** A period that has not started shows an em dash, not N/A. */
  const val = (r: ManagerRow, m: any, kind: "money" | "percent" = "money") =>
    r.has_started
      ? <Value m={m} kind={kind} />
      : <span className="not-yet" title="This period has not started yet.">{NOT_YET}</span>;

  const columns = [
    {
      key: "canonical_manager",
      label: "Manager",
      render: (r: ManagerRow) => (
        <>
          {r.canonical_manager}
          {!r.include_in_rankings && (
            <span className="chip" title="Excluded from rankings by default. Actual income still counts towards business totals.">
              {r.status === "legacy_unmapped" ? "legacy" : r.status}
            </span>
          )}
        </>
      ),
    },
    ...(period === "month"
      ? [{ key: "period_month", label: "Month",
           render: (r: ManagerRow) => monthAU(r.period_month) }]
      : period === "quarter"
      ? [{ key: "financial_quarter", label: "Quarter",
           render: (r: ManagerRow) =>
             r.financial_quarter
               ? <>Q{r.financial_quarter} <span className="qtr-fy">{fyLabel}</span></>
               : "\u2014" }]
      : [{ key: "financial_year", label: "Period",
           render: () => <>{period === "ytd" ? "Year to date" : "Full year"}{" "}
                          <span className="qtr-fy">{fyLabel}</span></> }]),
    { key: "original_forecast", label: "Renewal Forecast", align: "right" as const,
      render: (r: ManagerRow) => <Value m={r.original_forecast} /> },
    { key: "positive_actual_income", label: "Positive Actual", align: "right" as const,
      render: (r: ManagerRow) => val(r, r.positive_actual_income) },
    { key: "return_income", label: "Return Income", align: "right" as const,
      render: (r: ManagerRow) => val(r, r.return_income) },
    { key: "net_actual_income", label: "Net Actual", align: "right" as const,
      render: (r: ManagerRow) => val(r, r.net_actual_income) },
    { key: "new_business_growth_target", label: "NB Target", align: "right" as const,
      render: (r: ManagerRow) => <Value m={r.new_business_growth_target} /> },
    { key: "total_budget", label: "Total Budget", align: "right" as const,
      render: (r: ManagerRow) => <Value m={r.total_budget} /> },
    { key: "budget_variance", label: "Variance", align: "right" as const,
      render: (r: ManagerRow) => val(r, r.budget_variance) },
    { key: "budget_to_date", label: "Budget to date", align: "right" as const,
      hint: "Budget for the months elapsed. A quarter one month in is measured against one month of budget, not three.",
      render: (r: ManagerRow) => val(r, r.budget_to_date) },
    { key: "budget_verdict", label: "Result", align: "left" as const,
      render: (r: ManagerRow) =>
        !r.has_started
          ? <span className="not-yet">{NOT_YET}</span>
          : r.budget_verdict === "Not measurable"
          ? <span className="na" title="No budget applies for this period.">Not measurable</span>
          : <span className={`verdict-chip ${r.budget_verdict === "Made budget" ? "made" : "below"}`}>
              {r.budget_verdict}
              {r.over_or_under_pct?.available && (
                <b>{Number(r.over_or_under_pct.value) >= 0 ? "+" : ""}
                   {(Number(r.over_or_under_pct.value) * 100).toFixed(1)}%</b>
              )}
            </span> },
    { key: "budget_achievement", label: "Budget %", align: "right" as const,
      render: (r: ManagerRow) => val(r, r.budget_achievement, "percent") },
    { key: "renewal_achievement", label: "Renewal %", align: "right" as const,
      hint: "Actual RWL/TRW income against the Original Renewal Forecast. N/A where no usable baseline exists.",
      render: (r: ManagerRow) => val(r, r.renewal_achievement, "percent") },
    { key: "actual_new_business", label: "Actual NB", align: "right" as const,
      render: (r: ManagerRow) => val(r, r.actual_new_business) },
    { key: "latest_outlook", label: "Outlook", align: "right" as const,
      render: (r: ManagerRow) => <Value m={r.latest_outlook} /> },
    { key: "remaining_budget_gap", label: "Gap", align: "right" as const,
      render: (r: ManagerRow) => <Value m={r.remaining_budget_gap} /> },
    { key: "renewal_income", label: "Renewal income", align: "right" as const,
      hint: "Actual RWL and TRW income for the months elapsed.",
      render: (r: ManagerRow) => val(r, r.renewal_income) },
  ];

  return (
    <>
      <h1>Compare managers <span className="fy">{fyLabel}</span></h1>
      <GstBanner meta={d.meta} />
      <Panel
        title={`Performance — ${PERIODS.find((p) => p.key === period)!.label}, ${fyLabel}`}
        subtitle="Inactive, legacy and unmapped managers are out of rankings by default. Their actual income still counts towards business totals."
        actions={
          <div className="controls">
            <label>Compare by
              <select value={period} onChange={(e) => setPeriod(e.target.value)}>
                {PERIODS.map((p) => (
                  <option key={p.key} value={p.key}>{p.label}</option>
                ))}
              </select>
            </label>
            <label>Financial year
              <select value={fy} onChange={(e) => setFy(Number(e.target.value))}>
                <YearOptions years={years} />
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={includeNonRanked}
                     onChange={(e) => setIncludeNonRanked(e.target.checked)} />
              Show non-ranked managers
            </label>
            <a className="button" href={api.exportUrl("managers", "csv", params)}>Export CSV</a>
          </div>
        }
      >
        <DataTable columns={columns} rows={d.items} caption="manager performance" />
      </Panel>
    </>
  );
}
