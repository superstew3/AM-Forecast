import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
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
export function MonthLocks({ manager, months }) {
    const qc = useQueryClient();
    const [reason, setReason] = useState("");
    const lock = useMutation({
        mutationFn: (body) => api.post(`/api/budget/${body.unlock ? "unlock" : "lock"}`, {
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
    return (_jsxs(_Fragment, { children: [_jsxs("label", { className: "reason", children: ["Reason (required to lock or unlock)", _jsx("input", { value: reason, onChange: (e) => setReason(e.target.value), placeholder: "e.g. agreed at the September review" })] }), _jsx("div", { className: "lock-grid", children: months.map((r) => (_jsxs("div", { className: `lock-cell${r.is_locked ? " locked" : ""}`, children: [_jsx("span", { className: "lock-month", children: monthAU(r.forecast_month) }), _jsx("span", { className: "lock-budget", children: money({ value: r.total_budget, available: true }) }), _jsx("button", { disabled: !ready || lock.isPending, title: ready ? undefined : "Enter a reason first", onClick: () => lock.mutate({ target_month: r.forecast_month,
                                unlock: r.is_locked }), children: r.is_locked ? "Unlock" : "Lock" })] }, r.forecast_month))) }), lock.isError && _jsx(Failed, { error: lock.error })] }));
}
