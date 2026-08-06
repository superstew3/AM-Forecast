import React from "react";
import {
  GST_NOTE, Meta, Money, NA, Ratio, isUnavailable, money, percent, reasonFor, tone,
} from "../lib/api";

/** A value that may be unavailable. Renders N/A with the reason as a tooltip. */
export function Value({ m, kind = "money", digits }: {
  m: Money | Ratio | null | undefined;
  kind?: "money" | "percent" | "count";
  digits?: number;
}) {
  const unavailable = isUnavailable(m);
  const text =
    kind === "percent" ? percent(m as Ratio, digits) :
    kind === "count" ? (unavailable ? NA : String(m!.value)) :
    money(m as Money);
  // Negatives read red as well as bracketed. Brackets alone are easy to miss in
  // a dense grid, and a return is the thing you most want to notice.
  const negative = !unavailable && Number(m!.value) < 0;
  return (
    <span
      className={unavailable ? "na" : `val${negative ? " negative" : ""}`}
      title={unavailable ? (reasonFor(m) ?? "Not available") : undefined}
      data-available={!unavailable}
    >
      {text}
      {unavailable && <span className="na-mark" aria-hidden="true">?</span>}
    </span>
  );
}

export function Metric({ label, m, kind = "money", hint, emphasis, ratio }: {
  label: string;
  m: Money | Ratio | null | undefined;
  kind?: "money" | "percent";
  hint?: string;
  emphasis?: boolean;
  ratio?: Ratio;
}) {
  const t = ratio ? tone(ratio) : "none";
  return (
    <div className={`metric${emphasis ? " metric-emphasis" : ""} tone-${t}`}>
      <div className="metric-label">
        {label}
        {hint && <span className="hint" title={hint}>i</span>}
      </div>
      <div className="metric-value"><Value m={m} kind={kind} /></div>
      {ratio && (
        <div className="metric-sub">
          Achievement <Value m={ratio} kind="percent" />
        </div>
      )}
    </div>
  );
}

export function GstBanner({ meta }: { meta?: Meta }) {
  return (
    <div className="gst-banner">
      <strong>{GST_NOTE}</strong>
      {meta && (
        <span className="gst-meta">
          Reporting cut-off {meta.cut_off_date} &middot; {meta.timezone}
        </span>
      )}
    </div>
  );
}

export function Notes({ notes }: { notes?: string[] }) {
  if (!notes?.length) return null;
  return (
    <ul className="notes">
      {notes.map((n) => <li key={n}>{n}</li>)}
    </ul>
  );
}

export function Panel({ title, subtitle, actions, children }: {
  title: string; subtitle?: string; actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="subtitle">{subtitle}</p>}
        </div>
        {actions && <div className="panel-actions">{actions}</div>}
      </header>
      {children}
    </section>
  );
}

export function Loading({ what }: { what: string }) {
  return <div className="state loading">Loading {what}…</div>;
}

export function Empty({ what }: { what: string }) {
  return <div className="state empty">No {what} for the current filters.</div>;
}

export function Failed({ error, retry }: { error: unknown; retry?: () => void }) {
  return (
    <div className="state error" role="alert">
      <strong>Could not load this view.</strong>
      <div>{error instanceof Error ? error.message : String(error)}</div>
      {retry && <button onClick={retry}>Try again</button>}
    </div>
  );
}

/**
 * A table whose totals come from the server.
 *
 * `total` is passed in from the API response, never computed by summing the
 * rows on screen. Summing the visible page would silently understate every
 * total the moment pagination kicks in.
 */
export function DataTable({ columns, rows, serverTotals, caption, onRowClick }: {
  columns: { key: string; label: string; render?: (row: any) => React.ReactNode;
             align?: "left" | "right"; hint?: string }[];
  rows: any[];
  serverTotals?: Record<string, React.ReactNode>;
  caption?: string;
  onRowClick?: (row: any) => void;
}) {
  if (!rows.length) return <Empty what={caption ?? "records"} />;
  return (
    <div className="table-wrap">
      <table>
        {caption && <caption>{caption}</caption>}
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={c.align === "right" ? "right" : ""}>
                {c.label}
                {c.hint && <span className="hint" title={c.hint}>i</span>}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={row.id ?? row.policy_id ?? row.canonical_manager ?? i}
                onClick={onRowClick ? () => onRowClick(row) : undefined}
                className={onRowClick ? "clickable" : ""}>
              {columns.map((c) => {
                const rendered = c.render ? c.render(row) : String(row[c.key] ?? NA);
                // Negatives read red as well as bracketed. Brackets alone are
                // easy to miss in a dense grid, and a return is the thing you
                // most want to notice.
                const negative = typeof rendered === "string"
                  && /^\(\s*[$-]/.test(rendered.trim());
                return (
                  <td key={c.key}
                      className={`${c.align === "right" ? "right" : ""}${
                        negative ? " negative" : ""}`}>
                    {rendered}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
        {serverTotals && (
          <tfoot>
            <tr>
              {columns.map((c, i) => (
                <td key={c.key} className={c.align === "right" ? "right" : ""}>
                  {i === 0 ? "Total (all rows, from server)" : serverTotals[c.key] ?? ""}
                </td>
              ))}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}

export function BaselineWarning({ month, source, exceptions }: {
  month: string; source?: string; exceptions?: string[];
}) {
  return (
    <div className="warning" role="note">
      <strong>{month} uses supplied forecast figures.</strong>{" "}
      {source ?? "A forecast per manager was entered directly"}, held at
      manager-month level, because the Renewals Pending file was extracted after
      most of that month's renewals had already transacted. Actuals come from
      Sales Transactions. Policy-level renewal detail begins August 2026.
      {exceptions?.length ? (
        <> No forecast is recorded for {exceptions.join(", ")}, which show N/A.</>
      ) : null}
    </div>
  );
}
