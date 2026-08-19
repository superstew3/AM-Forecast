import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, money, monthAU, percent } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel } from "../components/ui";

/* The budget page.
 *
 * Rebuilt around the thing it exists to do: see what growth assumption every
 * manager is on, and change one without moving anybody else.
 *
 * Before, the rate was set through a form detached from the figures it changed
 * -- pick a scope, pick a manager, pick a quarter, type a number, and read the
 * result somewhere in a 56-row table below. The year was hard-coded to 2026,
 * there was no way to look at one quarter, and the monthly view put 168 rows on
 * screen at once. The audit trail rendered as raw JSON.
 *
 * Now: pick a period, see one row per manager, and edit the rate on the row it
 * belongs to. The scope follows the period, so changing a rate while looking at
 * Q2 writes a Q2 override and nothing else moves.
 */

const PERIODS = [
  { key: "ytd", label: "Year to date" },
  { key: "1", label: "Q1 Jul-Sep" },
  { key: "2", label: "Q2 Oct-Dec" },
  { key: "3", label: "Q3 Jan-Mar" },
  { key: "4", label: "Q4 Apr-Jun" },
  { key: "all", label: "Full year" },
];

type Draft = { manager: string; pct: string; reason: string } | null;

export default function Budget() {
  const qc = useQueryClient();
  const { years, currentFy } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy;
  const [period, setPeriod] = useState("all");
  const [draft, setDraft] = useState<Draft>(null);
  const [globalDraft, setGlobalDraft] = useState<{ pct: string; reason: string } | null>(null);
  const [drill, setDrill] = useState<string | null>(null);

  const q = useQuery({
    queryKey: ["budget", fy],
    queryFn: () => api.budget(fy!),
    enabled: fy != null,
  });
  const audit = useQuery({ queryKey: ["budget-audit"], queryFn: api.budgetAudit });

  const save = useMutation({
    mutationFn: (body: any) => api.post("/api/budget/growth-rate", body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["budget"] });
      qc.invalidateQueries({ queryKey: ["budget-audit"] });
      setDraft(null);
      setGlobalDraft(null);
    },
  });

  // Guard on the data, not on isLoading: a query disabled while it waits for the
  // financial year is not "loading", so isLoading alone falls through to
  // undefined and the page crashes before it renders.
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  if (fy == null || !q.data) return <Loading what="budget" />;
  const d = q.data;

  const quarter = period === "ytd" || period === "all" ? null : Number(period);
  const rows = rollup(d.quarters, quarter);
  const label = PERIODS.find((p) => p.key === period)?.label ?? "Full year";

  // The scope a change will be written at, derived from what is on screen. It is
  // stated in the interface rather than chosen from a menu, because the menu was
  // the part people got wrong -- and getting it wrong meant moving everybody.
  const scopeForEdit = quarter ? "manager_quarter" : "manager";
  const scopeWords = quarter
    ? `${label} only, for that manager`
    : "every quarter of this year, for that manager";

  const globalRate = d.active_rates?.find((r: any) => r.scope === "global");
  const overridden = new Set(
    d.quarters.filter((r: any) => r.growth_basis !== "global")
      .map((r: any) => r.canonical_manager));
  const managerCount = new Set(d.quarters.map((r: any) => r.canonical_manager)).size;

  return (
    <>
      <h1>
        Budget
        <span className="fy">FY{fy}-{String(fy + 1).slice(2)}</span>
        <select className="inline-select" value={fy}
                onChange={(e) => setFyPick(Number(e.target.value))}>
          <YearOptions years={years} />
        </select>
      </h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />

      <div className="quarter-bar">
        <div className="quarter-tabs">
          {PERIODS.map((p) => (
            <button key={p.key} onClick={() => setPeriod(p.key)}
                    className={`quarter-tab${period === p.key ? " is-active" : ""}`}>
              {p.label}
            </button>
          ))}
        </div>
        <div className="quarter-progress">
          Editing a rate here writes it for {scopeWords}.
        </div>
      </div>

      {/* The business-wide rate, on its own and clearly labelled with how many
          managers it actually reaches. Changing it used to be one option in a
          dropdown next to "Manager", which is a thin line between adjusting one
          person and adjusting everyone. */}
      <Panel title="Business-wide growth goal"
             subtitle="The default every manager inherits unless they have their own rate.">
        <div className="global-rate">
          <div className="global-rate-figure">
            <div className="metric-label">Current</div>
            <div className="metric-value">
              {globalRate ? percent({ value: globalRate.growth_pct, available: true }) : "—"}
            </div>
            <div className="metric-sub">
              Applies to {managerCount - overridden.size} of {managerCount} managers.
              {overridden.size > 0 && <> {overridden.size} have their own rate and will not move.</>}
            </div>
          </div>
          {globalDraft ? (
            <div className="rate-edit">
              <label>New rate
                <input type="number" step="0.005" value={globalDraft.pct} autoFocus
                       onChange={(e) => setGlobalDraft({ ...globalDraft, pct: e.target.value })} />
              </label>
              <label className="grow">Reason
                <input value={globalDraft.reason} placeholder="Recorded against the change"
                       onChange={(e) => setGlobalDraft({ ...globalDraft, reason: e.target.value })} />
              </label>
              <button className="btn-primary"
                      disabled={save.isPending || globalDraft.reason.trim().length < 3}
                      onClick={() => save.mutate({
                        scope: "global", growth_pct: Number(globalDraft.pct),
                        reason: globalDraft.reason })}>
                Apply to {managerCount - overridden.size} managers
              </button>
              <button onClick={() => setGlobalDraft(null)}>Cancel</button>
            </div>
          ) : (
            <button onClick={() => setGlobalDraft({
              pct: String(globalRate?.growth_pct ?? 0.075), reason: "" })}>
              Change the business-wide goal
            </button>
          )}
        </div>
      </Panel>

      <Panel title={`${label} — by manager`}
             subtitle="Original forecast, the growth applied to it, and the resulting target. Change a rate on the row it belongs to.">
        <DataTable
          caption="managers"
          rows={rows}
          columns={[
            { key: "canonical_manager", label: "Manager" },
            { key: "growth_pct", label: "Growth goal", align: "right",
              render: (r: any) => (
                draft && draft.manager === r.canonical_manager ? (
                  <input className="rate-input" type="number" step="0.005" autoFocus
                         value={draft.pct}
                         onChange={(e) => setDraft({ ...draft, pct: e.target.value })} />
                ) : (
                  <button className="rate-button"
                          onClick={() => setDraft({
                            manager: r.canonical_manager,
                            pct: String(r.growth_pct ?? 0.075), reason: "" })}>
                    {r.growth_pct == null ? "N/A"
                      : percent({ value: r.growth_pct, available: true })}
                  </button>
                )) },
            { key: "growth_basis", label: "From",
              hint: "Which level of the hierarchy supplied the rate. Most specific wins: manager and quarter, then manager, then the business-wide default.",
              render: (r: any) => <span className={`chip chip-${r.growth_basis}`}>{r.growth_basis}</span> },
            { key: "original_renewal_forecast", label: "Original forecast", align: "right",
              render: (r: any) => money({ value: r.original_renewal_forecast, available: true }) },
            { key: "new_business_growth_target", label: "Growth target", align: "right",
              render: (r: any) => money({ value: r.new_business_growth_target, available: true }) },
            { key: "total_budget", label: "Budget target", align: "right",
              render: (r: any) => money({ value: r.total_budget, available: true }) },
            { key: "locked_months", label: "", align: "right",
              render: (r: any) => r.locked_months
                ? <span className="chip chip-locked" title="Months already measured against. A rate change will not move them.">
                    {r.locked_months} locked</span>
                : null },
            { key: "drill", label: "",
              render: (r: any) => (
                <button className="link-button"
                        onClick={() => setDrill(drill === r.canonical_manager
                                                ? null : r.canonical_manager)}>
                  {drill === r.canonical_manager ? "Hide months" : "Months"}
                </button>) },
          ]}
        />

        {/* The edit row appears under the table rather than inside it: a form
            inside a cell reflows the whole grid as you type. */}
        {draft && (
          <div className="rate-edit rate-edit-standalone">
            <span className="rate-edit-who">{draft.manager}</span>
            <label className="grow">Reason
              <input value={draft.reason} placeholder="Recorded against the change"
                     onChange={(e) => setDraft({ ...draft, reason: e.target.value })} />
            </label>
            <button className="btn-primary"
                    disabled={save.isPending || draft.reason.trim().length < 3}
                    onClick={() => save.mutate({
                      scope: scopeForEdit,
                      canonical_manager: draft.manager,
                      financial_year: quarter ? fy : null,
                      financial_quarter: quarter,
                      growth_pct: Number(draft.pct),
                      reason: draft.reason })}>
              Save for {scopeWords}
            </button>
            <button onClick={() => setDraft(null)}>Cancel</button>
          </div>
        )}
        {save.isError && (
          <p className="error-note">{String((save.error as any)?.message ?? save.error)}</p>
        )}
      </Panel>

      {/* Months for one manager, on request. The full monthly table put 168 rows
          on the page, which nobody read. */}
      {drill && (
        <Panel title={`${drill} — monthly allocation`}
               subtitle="The quarterly target is spread by each month's share of that quarter's original forecast, not in equal thirds.">
          <DataTable
            caption="months"
            rows={d.monthly.filter((m: any) => m.canonical_manager === drill
              && (quarter == null || m.financial_quarter === quarter))}
            columns={[
              { key: "forecast_month", label: "Month",
                render: (r: any) => monthAU(r.forecast_month) },
              { key: "original_forecast", label: "Original forecast", align: "right",
                render: (r: any) => money({ value: r.original_forecast, available: true }) },
              { key: "new_business_growth_target", label: "Growth target", align: "right",
                render: (r: any) => money({ value: r.new_business_growth_target, available: true }) },
              { key: "total_budget", label: "Budget target", align: "right",
                render: (r: any) => money({ value: r.total_budget, available: true }) },
              { key: "is_overridden", label: "",
                render: (r: any) => r.is_overridden
                  ? <span className="chip" title={r.override_reason ?? ""}>overridden</span>
                  : r.is_locked ? <span className="chip chip-locked">locked</span> : null },
            ]}
          />
        </Panel>
      )}

      <Panel title="What has been changed"
             subtitle="Every rate change, who made it and why.">
        {audit.data && (
          <DataTable
            caption="changes"
            rows={audit.data.items}
            columns={[
              { key: "performed_at", label: "When",
                render: (r: any) => new Date(r.performed_at)
                  .toLocaleString("en-AU", { dateStyle: "medium", timeStyle: "short" }) },
              { key: "performed_by", label: "By" },
              { key: "canonical_manager", label: "Applied to",
                render: (r: any) => r.canonical_manager ?? "every manager" },
              { key: "scope_description", label: "Scope" },
              // Was JSON.stringify of the whole object, which put
              // {"growth_pct": "0.0750"} in a table cell. The rate is the part
              // anybody reads.
              { key: "change", label: "Rate", align: "right",
                render: (r: any) => {
                  const before = r.before_value?.growth_pct;
                  const after = r.after_value?.growth_pct;
                  if (after == null) return "\u2014";
                  return before == null
                    ? percent({ value: after, available: true })
                    : <>{percent({ value: before, available: true })}
                       {" \u2192 "}{percent({ value: after, available: true })}</>;
                } },
              { key: "reason", label: "Reason" },
            ]}
          />
        )}
      </Panel>
    </>
  );
}

/** One row per manager for the chosen period. A plain function, not a hook:
 * it is called after the loading guards, where a hook would break the rules of
 * hooks and crash on the first render that returns early.
 *
 * A single quarter passes through untouched. Year to date and full year sum the
 * money and recompute the rate from the totals rather than averaging the
 * quarterly percentages -- averaging them weights a small quarter the same as a
 * large one and produces a rate nobody is actually on. Where the quarters
 * disagree the basis reads "mixed", which is the honest answer and a signal that
 * an override exists somewhere inside the period.
 */
function rollup(quarters: any[], quarter: number | null) {
    if (quarter) return quarters.filter((r: any) => r.financial_quarter === quarter);
    const by = new Map<string, any>();
    for (const r of quarters) {
      const k = r.canonical_manager;
      const a = by.get(k) ?? {
        canonical_manager: k, original_renewal_forecast: 0,
        new_business_growth_target: 0, total_budget: 0, locked_months: 0,
        bases: new Set<string>(), pcts: new Set<string>(),
      };
      a.original_renewal_forecast += Number(r.original_renewal_forecast ?? 0);
      a.new_business_growth_target += Number(r.new_business_growth_target ?? 0);
      a.total_budget += Number(r.total_budget ?? 0);
      a.locked_months += Number(r.locked_months ?? 0);
      a.bases.add(r.growth_basis);
      if (r.growth_pct != null) a.pcts.add(String(r.growth_pct));
      by.set(k, a);
    }
    return [...by.values()].map((a) => ({
      ...a,
      growth_pct: a.original_renewal_forecast > 0
        ? a.new_business_growth_target / a.original_renewal_forecast : null,
      growth_basis: a.bases.size === 1 ? [...a.bases][0] : "mixed",
    })).sort((x, y) => x.canonical_manager.localeCompare(y.canonical_manager));
}
