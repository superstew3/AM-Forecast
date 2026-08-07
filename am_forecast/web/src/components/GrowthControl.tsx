import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { Failed } from "./ui";

/**
 * Budget growth control.
 *
 * Collapsed by default: the figure in force is what matters day to day, and the
 * form behind it is used rarely. Scope is whole year or a named quarter —
 * month-level rates exist in the data model but are not offered here, because
 * twelve individually set months is a maintenance burden that produces a budget
 * nobody can explain.
 *
 * The manager is fixed to the one being viewed and is never a free-text field,
 * so a change cannot be applied to somebody else by mistake.
 */
export function GrowthControl({ manager, financialYear, activePct, activeBasis,
                                quarterGrowth }: {
  manager: string;
  financialYear: number;
  activePct: { value: string | number | null; available: boolean } | null;
  activeBasis: string | null;
  quarterGrowth: any[];
}) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<"year" | "quarter">("year");
  const [quarter, setQuarter] = useState(1);
  const [pct, setPct] = useState("");
  const [reason, setReason] = useState("");

  const save = useMutation({
    mutationFn: () => api.post("/api/budget/growth-rate",
      scope === "quarter"
        ? {
            scope: "manager_quarter", canonical_manager: manager,
            financial_year: financialYear, financial_quarter: quarter,
            // Entered as a percentage, stored as a rate.
            growth_pct: Number(pct) / 100, dollar_override: null, reason,
          }
        : {
            scope: "manager", canonical_manager: manager,
            financial_year: financialYear,
            growth_pct: Number(pct) / 100, dollar_override: null, reason,
          }),
    onSuccess: () => {
      // Refresh figures without disturbing which manager is on screen.
      qc.invalidateQueries({ queryKey: ["manager-detail"] });
      qc.invalidateQueries({ queryKey: ["budget"] });
      qc.invalidateQueries({ queryKey: ["yoy-mgr"] });
      qc.invalidateQueries({ queryKey: ["bonus"] });
      setPct(""); setReason(""); setOpen(false);
    },
  });

  const mixed = new Set(quarterGrowth.map((q) => String(q.growth_pct))).size > 1;

  return (
    <div className="growth-control">
      <div className="growth-headline">
        <div>
          <span className="growth-caption">Budget growth for {manager}</span>
          <span className="growth-figure">
            {activePct?.available
              ? `${(Number(activePct.value) * 100).toFixed(2)}%`
              : "N/A"}
            {mixed && <span className="chip">varies by quarter</span>}
          </span>
          <span className="growth-source">set at {activeBasis ?? "default"} level</span>
        </div>
        <button className="growth-toggle" onClick={() => setOpen(!open)}>
          {open ? "Cancel" : "Change"}
        </button>
      </div>

      <div className="growth-quarters">
        {quarterGrowth.map((q) => (
          <span key={q.financial_quarter} className={q.growth_basis === "manager_quarter"
            ? "growth-q overridden" : "growth-q"}>
            Q{q.financial_quarter}
            <strong>
              {q.growth_pct === null ? "$ override"
                : `${(Number(q.growth_pct) * 100).toFixed(2)}%`}
            </strong>
          </span>
        ))}
      </div>

      {open && (
        <div className="growth-form">
          <p className="growth-explain">
            Budget = Renewal Forecast + (Renewal Forecast &times; growth&nbsp;%).
            This changes <strong>{manager}</strong> only, and changes the budget
            only — the renewal forecast is never affected.
          </p>
          <div className="form-row">
            <label>
              Apply to
              <select value={scope} onChange={(e) => setScope(e.target.value as any)}>
                <option value="year">Whole year</option>
                <option value="quarter">One quarter</option>
              </select>
            </label>
            {scope === "quarter" && (
              <label>
                Quarter
                <select value={quarter} onChange={(e) => setQuarter(Number(e.target.value))}>
                  <option value={1}>Q1 Jul-Sep</option>
                  <option value={2}>Q2 Oct-Dec</option>
                  <option value={3}>Q3 Jan-Mar</option>
                  <option value={4}>Q4 Apr-Jun</option>
                </select>
              </label>
            )}
            <label>
              Growth %
              <input value={pct} onChange={(e) => setPct(e.target.value)}
                     placeholder="7.5" inputMode="decimal" />
            </label>
            <label className="grow">
              Reason (required)
              <input value={reason} onChange={(e) => setReason(e.target.value)}
                     placeholder="why this target is changing" />
            </label>
            <button className="primary"
                    disabled={!pct || reason.length < 3 || save.isPending}
                    onClick={() => save.mutate()}>
              {save.isPending ? "Saving…" : "Apply"}
            </button>
          </div>
          {save.isError && <Failed error={save.error} />}
          <p className="footnote">
            Recorded with your name, the reason and the previous value.
          </p>
        </div>
      )}
    </div>
  );
}
