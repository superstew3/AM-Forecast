import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { useState } from "react";
import { Failed, GstBanner, Loading, Panel, Value } from "../components/ui";

/**
 * Account manager index.
 *
 * The manager area used to open on whichever name sorted first, which quietly
 * implied that manager mattered more than the rest. It now opens on everyone,
 * and you choose.
 */
export default function ManagerIndex() {
  const { years, currentFy, label } = usePeriods();
  const [fyPick, setFyPick] = useState<number | null>(null);
  const fy = fyPick ?? currentFy ?? new Date().getFullYear();
  const [showAll, setShowAll] = useState(false);

  const params = new URLSearchParams({
    period: "ytd", financial_year: String(fy),
    include_non_ranked: String(showAll),
  });
  const q = useQuery({
    queryKey: ["managers", "ytd", fy, showAll],
    queryFn: () => api.managers(params),
  });

  if (q.isLoading) return <Loading what="account managers" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const rows = q.data!.items;

  return (
    <>
      <h1>
        Account managers
        <span className="fy">{label(fy)} year to date</span>
        <select className="inline-select" value={fy}
                onChange={(e) => setFyPick(Number(e.target.value))}>
          <YearOptions years={years} />
        </select>
      </h1>
      <GstBanner meta={q.data!.meta} />

      <Panel
        title="Choose a manager"
        subtitle="Year-to-date position for each. Open one for the full month-by-month view, budget growth and bonus."
        actions={
          <label className="check">
            <input type="checkbox" checked={showAll}
                   onChange={(e) => setShowAll(e.target.checked)} />
            Include non-ranked
          </label>
        }
      >
        <div className="manager-cards">
          {rows.map((r) => {
            const made = r.budget_verdict === "Made budget";
            const measurable = r.budget_achievement?.available;
            return (
              <Link key={r.canonical_manager} className="manager-card"
                    to={`/manager?name=${encodeURIComponent(r.canonical_manager)}`}>
                <div className="manager-card-head">
                  <strong>{r.canonical_manager}</strong>
                  {!r.include_in_rankings && <span className="chip">not ranked</span>}
                </div>
                <div className="manager-card-figure">
                  <Value m={r.net_actual_income} />
                  <span className="manager-card-caption">net actual, year to date</span>
                </div>
                <div className="manager-card-row">
                  <span>Budget</span><Value m={r.budget_to_date} />
                </div>
                <div className="manager-card-row">
                  <span>Renewal</span>
                  <Value m={r.renewal_achievement} kind="percent" />
                </div>
                <div className={`manager-card-verdict ${
                  !measurable ? "neutral" : made ? "good" : "bad"}`}>
                  {measurable ? (
                    <>
                      {r.budget_verdict}
                      {r.over_or_under_pct?.available && (
                        <b>{Number(r.over_or_under_pct.value) >= 0 ? "+" : ""}
                          {(Number(r.over_or_under_pct.value) * 100).toFixed(1)}%</b>
                      )}
                    </>
                  ) : "Not measurable yet"}
                </div>
              </Link>
            );
          })}
        </div>
      </Panel>
    </>
  );
}
