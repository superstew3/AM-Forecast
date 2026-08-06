/**
 * Charts.
 *
 * Hand-rolled SVG rather than a charting library, for two reasons: the static
 * preview has to render identically without a bundler, and every chart here is
 * simple enough that a library would be more configuration than code.
 *
 * Charts render values the API has already computed. They never aggregate.
 * A month with no data is drawn as absent, not as a zero-height bar — a bar of
 * zero says "we earned nothing", which is a different claim from "this month
 * has not happened".
 */
import { useState } from "react";

const fmtShort = (n: number) => {
  const abs = Math.abs(n);
  if (abs >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${Math.round(n / 1000)}k`;
  return n.toFixed(0);
};

/** Negatives read the same way everywhere: accounting parentheses. */
const fmtFull = (n: number) => {
  const text = new Intl.NumberFormat("en-AU", {
    style: "currency", currency: "AUD", maximumFractionDigits: 0,
  }).format(Math.abs(n));
  return n < 0 ? `(${text})` : text;
};

export interface SeriesPoint {
  label: string;
  actual?: number | null;
  budget?: number | null;
  prior?: number | null;
  started?: boolean;
}

/**
 * Actual against budget by month, with prior year as a reference line.
 * Bars are omitted, not zeroed, where a month has not started.
 */
export function MonthlyBars({ data, height = 240, onSelect, selected }: {
  data: SeriesPoint[];
  height?: number;
  onSelect?: (label: string) => void;
  selected?: string | null;
}) {
  const [hover, setHover] = useState<number | null>(null);
  const values = data.flatMap((d) =>
    [d.actual, d.budget, d.prior].filter((v): v is number => v != null && !Number.isNaN(v)));
  const max = Math.max(1, ...values.map(Math.abs));
  const w = 100 / data.length;
  const pad = 34;
  const plot = height - pad;

  const scale = (v: number) => (Math.abs(v) / max) * (plot - 12);

  return (
    <div className="chart">
      <svg viewBox={`0 0 100 ${height}`} preserveAspectRatio="none"
           role="img" aria-label="Actual against budget by month">
        {[0.25, 0.5, 0.75, 1].map((f) => (
          <line key={f} x1="0" x2="100" y1={plot - f * (plot - 12)}
                y2={plot - f * (plot - 12)} className="grid-line" />
        ))}
        {data.map((d, i) => {
          const x = i * w;
          const isSel = selected === d.label;
          return (
            <g key={d.label}
               onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(null)}
               onClick={() => onSelect?.(d.label)}
               className={`bar-group${onSelect ? " clickable" : ""}${isSel ? " selected" : ""}`}>
              <rect x={x} y="0" width={w} height={plot} className="bar-hit" />
              {d.budget != null && (
                <rect x={x + w * 0.18} y={plot - scale(d.budget)}
                      width={w * 0.64} height={scale(d.budget)} className="bar-budget" />
              )}
              {d.actual != null && (
                <rect x={x + w * 0.3} y={plot - scale(d.actual)}
                      width={w * 0.4} height={scale(d.actual)}
                      className={`bar-actual${d.actual < 0 ? " negative" : ""}`} />
              )}
              {d.prior != null && (
                <line x1={x + w * 0.12} x2={x + w * 0.88}
                      y1={plot - scale(d.prior)} y2={plot - scale(d.prior)}
                      className="line-prior" />
              )}
            </g>
          );
        })}
      </svg>
      <div className="chart-axis">
        {data.map((d) => (
          <span key={d.label} className={d.started === false ? "future" : ""}>{d.label}</span>
        ))}
      </div>
      {hover !== null && data[hover] && (
        <div className="chart-tip">
          <strong>{data[hover]!.label}</strong>
          {data[hover]!.actual != null && <span>Actual {fmtFull(data[hover]!.actual!)}</span>}
          {data[hover]!.budget != null && <span>Budget {fmtFull(data[hover]!.budget!)}</span>}
          {data[hover]!.prior != null && <span>Prior yr {fmtFull(data[hover]!.prior!)}</span>}
          {data[hover]!.actual == null && data[hover]!.started === false &&
            <span className="muted">Not started</span>}
        </div>
      )}
      <div className="chart-legend">
        <span><i className="swatch bar-actual" />Actual</span>
        <span><i className="swatch bar-budget" />Budget</span>
        <span><i className="swatch line-prior" />Prior year</span>
        <span className="chart-scale">Peak {fmtShort(max)}</span>
      </div>
    </div>
  );
}

/** Horizontal composition bar: which categories make up a total. */
export function CompositionBar({ items, onSelect, selected }: {
  items: { label: string; value: number; share?: number | null }[];
  onSelect?: (label: string) => void;
  selected?: string | null;
}) {
  const total = items.reduce((s, i) => s + Math.abs(i.value), 0) || 1;
  return (
    <div className="composition">
      <div className="composition-bar">
        {items.map((i, idx) => (
          <div key={i.label}
               className={`seg seg-${idx % 8}${selected === i.label ? " selected" : ""}${
                 onSelect ? " clickable" : ""}`}
               style={{ width: `${(Math.abs(i.value) / total) * 100}%` }}
               title={`${i.label}: ${fmtFull(i.value)} (${((Math.abs(i.value) / total) * 100).toFixed(1)}%)`}
               onClick={() => onSelect?.(i.label)} />
        ))}
      </div>
      <ul className="composition-key">
        {items.map((i, idx) => (
          <li key={i.label}
              className={`${selected === i.label ? "selected" : ""}${onSelect ? " clickable" : ""}`}
              onClick={() => onSelect?.(i.label)}>
            <i className={`swatch seg-${idx % 8}`} />
            <span className="key-label">{i.label}</span>
            <span className="key-value">{fmtFull(i.value)}</span>
            <span className="key-share">{((Math.abs(i.value) / total) * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Diverging bars for year-on-year movement: who is up, who is down. */
export function ChangeBars({ items, limit = 10 }: {
  items: { label: string; change: number }[];
  limit?: number;
}) {
  const shown = [...items]
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change))
    .slice(0, limit)
    .sort((a, b) => b.change - a.change);
  const max = Math.max(1, ...shown.map((i) => Math.abs(i.change)));
  return (
    <ul className="change-bars">
      {shown.map((i) => {
        const pct = (Math.abs(i.change) / max) * 50;
        return (
          <li key={i.label} title={`${i.label}: ${fmtFull(i.change)}`}>
            <span className="change-label">{i.label}</span>
            <span className="change-track">
              <span className="change-axis" />
              <span className={`change-fill ${i.change >= 0 ? "up" : "down"}`}
                    style={i.change >= 0
                      ? { left: "50%", width: `${pct}%` }
                      : { right: "50%", width: `${pct}%` }} />
            </span>
            <span className={`change-value ${i.change >= 0 ? "up" : "down"}`}>
              {i.change >= 0 ? "+" : ""}{fmtFull(i.change)}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Progress towards budget, with an explicit over/under verdict. */
export function BudgetGauge({ actual, budget, label }: {
  actual: number | null; budget: number | null; label?: string;
}) {
  if (actual == null || budget == null || budget === 0) {
    return <div className="gauge na-gauge">No budget applies, so achievement is N/A.</div>;
  }
  const ratio = actual / budget;
  const pct = Math.min(Math.abs(ratio), 1.5) / 1.5 * 100;
  const over = ratio >= 1;
  return (
    <div className={`gauge ${over ? "over" : "under"}`}>
      <div className="gauge-track">
        <span className="gauge-fill" style={{ width: `${pct}%` }} />
        <span className="gauge-target" style={{ left: `${(1 / 1.5) * 100}%` }} />
      </div>
      <div className="gauge-verdict">
        <strong>{(ratio * 100).toFixed(1)}%</strong> of {label ?? "budget"} —{" "}
        <span className={over ? "good" : "bad"}>
          {over ? "over budget" : "under budget"} by {fmtFull(Math.abs(actual - budget))}
          {" "}({Math.abs((ratio - 1) * 100).toFixed(1)}%)
        </span>
      </div>
    </div>
  );
}
