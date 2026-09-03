/**
 * Operational status.
 *
 * The routine this system asks for is two files a month. Knowing whether it had
 * been kept meant opening four screens and knowing what a healthy answer looked
 * like on each -- which is a skill, not a routine, and it decays.
 *
 * Every severity, headline, explanation and instruction on this page comes from
 * v_operational_status. Nothing is decided here. A rule restated in the
 * interface drifts from the one in the database, and the drift is invisible
 * because both sides look confident -- so this component reads and renders, and
 * that is all it does.
 */
import { useQuery } from "@tanstack/react-query";
import { api, dateAU } from "../lib/api";
import { Failed, Loading, Panel } from "../components/ui";

const WORDING: Record<string, string> = {
  ok: "Up to date",
  attention: "Due",
  action: "Needs action",
};

export default function Status() {
  const q = useQuery({ queryKey: ["status"], queryFn: api.status });

  if (q.isLoading) return <><h1>Operational status</h1><Loading what="status" /></>;
  if (q.isError) return <><h1>Operational status</h1><Failed error={q.error} retry={q.refetch} /></>;
  if (!q.data) return null;

  const { items, counts, overall, meta } = q.data;

  // Read from the counts the server sent rather than recounted here. Two places
  // counting the same rows is two places that can disagree about them.
  const summary =
    overall === "ok"
      ? "Everything is up to date. Nothing needs your attention."
      : [
          counts.action > 0 &&
            `${counts.action} need${counts.action === 1 ? "s" : ""} action`,
          counts.attention > 0 && `${counts.attention} due`,
        ]
          .filter(Boolean)
          .join(", ");

  return (
    <>
      <h1>Operational status</h1>

      <div className={`status-summary sev-${overall}`}>
        <strong>{summary}</strong>
        <span className="status-asat">
          Checked {new Date(meta.generated_at).toLocaleString("en-AU")} &middot;{" "}
          {meta.timezone}
          <button className="status-refresh" onClick={() => q.refetch()}
                  disabled={q.isFetching}>
            {q.isFetching ? "Checking…" : "Check again"}
          </button>
        </span>
      </div>

      <Panel
        title="The monthly routine"
        subtitle="Pull the Renewals Pending Summary in the last days of the month, and the Sales Transaction List in the first days of the next one. Everything below follows from those two files."
      >
        <ul className="status-list">
          {items.map((i) => (
            <li key={i.check_key} className={`status-item sev-${i.severity}`}>
              <span className="status-flag" aria-hidden="true" />
              <div className="status-body">
                <div className="status-line">
                  <span className="status-title">{i.title}</span>
                  {/* The severity in words as well as colour. Colour alone
                      excludes anyone who cannot distinguish these two reds, and
                      is invisible in a screenshot pasted into an email. */}
                  <span className="status-verdict">{WORDING[i.severity] ?? i.severity}</span>
                  {i.next_due && (
                    <span className="status-due">Next due {dateAU(i.next_due)}</span>
                  )}
                </div>
                <p className="status-headline">{i.headline}</p>
                <p className="status-detail">{i.detail}</p>
                <p className="status-todo">{i.what_to_do}</p>
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <p className="footnote">
        Every date here is the calendar's, in {meta.timezone}. The stored cut-off
        decides nothing and is not read. <strong>Due</strong> means something is
        coming up and no figure is wrong yet; <strong>needs action</strong> means
        a figure somewhere in the app is unavailable until it is dealt with.
      </p>
    </>
  );
}
