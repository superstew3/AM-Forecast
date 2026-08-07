import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api, money, monthAU } from "../lib/api";
import { Failed } from "./ui";

/**
 * Per-month budget locks.
 *
 * Separated from the growth control because they are different acts: one sets a
 * target, the other freezes it. Mixing them in one panel made both look
 * complicated.
 */
export function MonthLocks({ manager, months }: { manager: string; months: any[] }) {
  const qc = useQueryClient();
  const [reason, setReason] = useState("");

  const lock = useMutation({
    mutationFn: (body: { target_month: string; unlock: boolean }) =>
      api.post(`/api/budget/${body.unlock ? "unlock" : "lock"}`, {
        canonical_manager: manager, target_month: body.target_month, reason,
      }),
    onSuccess: () => {
      // Refresh figures without changing which manager is on screen.
      qc.invalidateQueries({ queryKey: ["budget"] });
      qc.invalidateQueries({ queryKey: ["manager-detail"] });
      setReason("");
    },
  });

  const ready = reason.trim().length >= 3;

  return (
    <>
      <label className="reason">
        Reason (required to lock or unlock)
        <input value={reason} onChange={(e) => setReason(e.target.value)}
               placeholder="e.g. agreed at the September review" />
      </label>
      <div className="lock-grid">
        {months.map((r: any) => (
          <div key={r.forecast_month}
               className={`lock-cell${r.is_locked ? " locked" : ""}`}>
            <span className="lock-month">{monthAU(r.forecast_month)}</span>
            <span className="lock-budget">
              {money({ value: r.total_budget, available: true })}
            </span>
            <button disabled={!ready || lock.isPending}
                    title={ready ? undefined : "Enter a reason first"}
                    onClick={() => lock.mutate({ target_month: r.forecast_month,
                                                 unlock: r.is_locked })}>
              {r.is_locked ? "Unlock" : "Lock"}
            </button>
          </div>
        ))}
      </div>
      {lock.isError && <Failed error={lock.error} />}
    </>
  );
}
