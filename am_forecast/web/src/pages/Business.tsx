import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { BaselineWarning, Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";

export default function Business() {
  const q = useQuery({ queryKey: ["business", 2026], queryFn: () => api.business(2026) });
  if (q.isLoading) return <Loading what="business performance" />;
  if (q.isError) return <Failed error={q.error} retry={() => q.refetch()} />;
  const d = q.data!;

  return (
    <>
      <h1>Overall business performance <span className="fy">FY2026-27</span></h1>
      <GstBanner meta={d.meta} />
      <Notes notes={d.meta.notes} />
      <BaselineWarning month="July 2026" source="Legacy Dashboard Forecast"
        exceptions={["Cameron Stewart", "Dinghy Scheme", "Anastasia K"]} />

      <Panel title="Position against budget"
             subtitle="Budget is the Original Renewal Forecast plus the new business growth target. It does not move when the Latest Forecast moves.">
        <div className="metric-grid">
          <Metric label="Net Actual Income" m={d.net_actual_income} emphasis
                  hint="Positive income plus signed return income. Includes returns." />
          <Metric label="Total Budget" m={d.total_budget} ratio={d.budget_achievement}
                  hint="Original Renewal Forecast + New Business Growth Target." />
          <Metric label="Latest Outlook" m={d.latest_outlook} emphasis
                  hint="Completed-period actuals plus Latest Forecast for future periods. Contains no assumed future new business." />
          <Metric label="Remaining Budget Gap" m={d.remaining_budget_gap}
                  hint="Income still to be found through new business, retention or other actual activity." />
        </div>
      </Panel>

      <Panel title="Actual income" subtitle="From Sales Transactions only.">
        <div className="metric-grid">
          <Metric label="Positive Actual Income" m={d.positive_actual_income} />
          <Metric label="Return Income" m={d.return_income}
                  hint="Absolute value of negative transactions, shown separately so income generation and income leakage are both visible." />
          <Metric label="Net Actual Income" m={d.net_actual_income} />
          <Metric label="Actual New Business" m={d.actual_new_business}
                  hint="Recognised only once it appears in Sales Transactions. Future new business is never forecast." />
        </div>
      </Panel>

      <Panel title="Renewal forecast"
             subtitle="Original is frozen at baseline. Latest reflects the newest accepted snapshot for future months.">
        <div className="metric-grid">
          <Metric label="Original Renewal Forecast" m={d.original_renewal_forecast} />
          <Metric label="Latest Renewal Forecast" m={d.latest_renewal_forecast}
                  hint="A completed month has no Latest Forecast; it reports actuals." />
          <Metric label="Forecast Movement" m={d.forecast_movement}
                  hint="Latest less Original. Removed policies reduce Latest but never create negative forecast income." />
        </div>
      </Panel>

      <Panel title="Where income was returned"
             subtitle="Each category is reported separately rather than as one lump of leakage.">
        <div className="metric-grid">
          <Metric label="Lapse / Lost Renewal" m={d.lapse_return_income} />
          <Metric label="Mid-Term Cancellation" m={d.midterm_cancellation_return_income} />
          <Metric label="New Business Cancellation" m={d.new_business_cancellation_return_income} />
          <Metric label="Negative Endorsements" m={d.negative_endorsements} />
          <Metric label="Endorsement Cancellations" m={d.endorsement_cancellations} />
        </div>
      </Panel>
    </>
  );
}
