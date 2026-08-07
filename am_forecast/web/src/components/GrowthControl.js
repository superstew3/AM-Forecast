import { jsxs as _jsxs, jsx as _jsx } from "react/jsx-runtime";
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
export function GrowthControl({ manager, financialYear, activePct, activeBasis, quarterGrowth }) {
    const qc = useQueryClient();
    const [open, setOpen] = useState(false);
    const [scope, setScope] = useState("year");
    const [quarter, setQuarter] = useState(1);
    const [pct, setPct] = useState("");
    const [reason, setReason] = useState("");
    const save = useMutation({
        mutationFn: () => api.post("/api/budget/growth-rate", scope === "quarter"
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
            setPct("");
            setReason("");
            setOpen(false);
        },
    });
    const mixed = new Set(quarterGrowth.map((q) => String(q.growth_pct))).size > 1;
    return (_jsxs("div", { className: "growth-control", children: [_jsxs("div", { className: "growth-headline", children: [_jsxs("div", { children: [_jsxs("span", { className: "growth-caption", children: ["Budget growth for ", manager] }), _jsxs("span", { className: "growth-figure", children: [activePct?.available
                                        ? `${(Number(activePct.value) * 100).toFixed(2)}%`
                                        : "N/A", mixed && _jsx("span", { className: "chip", children: "varies by quarter" })] }), _jsxs("span", { className: "growth-source", children: ["set at ", activeBasis ?? "default", " level"] })] }), _jsx("button", { className: "growth-toggle", onClick: () => setOpen(!open), children: open ? "Cancel" : "Change" })] }), _jsx("div", { className: "growth-quarters", children: quarterGrowth.map((q) => (_jsxs("span", { className: q.growth_basis === "manager_quarter"
                        ? "growth-q overridden" : "growth-q", children: ["Q", q.financial_quarter, _jsx("strong", { children: q.growth_pct === null ? "$ override"
                                : `${(Number(q.growth_pct) * 100).toFixed(2)}%` })] }, q.financial_quarter))) }), open && (_jsxs("div", { className: "growth-form", children: [_jsxs("p", { className: "growth-explain", children: ["Budget = Renewal Forecast + (Renewal Forecast \u00D7 growth\u00A0%). This changes ", _jsx("strong", { children: manager }), " only, and changes the budget only \u2014 the renewal forecast is never affected."] }), _jsxs("div", { className: "form-row", children: [_jsxs("label", { children: ["Apply to", _jsxs("select", { value: scope, onChange: (e) => setScope(e.target.value), children: [_jsx("option", { value: "year", children: "Whole year" }), _jsx("option", { value: "quarter", children: "One quarter" })] })] }), scope === "quarter" && (_jsxs("label", { children: ["Quarter", _jsxs("select", { value: quarter, onChange: (e) => setQuarter(Number(e.target.value)), children: [_jsx("option", { value: 1, children: "Q1 Jul-Sep" }), _jsx("option", { value: 2, children: "Q2 Oct-Dec" }), _jsx("option", { value: 3, children: "Q3 Jan-Mar" }), _jsx("option", { value: 4, children: "Q4 Apr-Jun" })] })] })), _jsxs("label", { children: ["Growth %", _jsx("input", { value: pct, onChange: (e) => setPct(e.target.value), placeholder: "7.5", inputMode: "decimal" })] }), _jsxs("label", { className: "grow", children: ["Reason (required)", _jsx("input", { value: reason, onChange: (e) => setReason(e.target.value), placeholder: "why this target is changing" })] }), _jsx("button", { className: "primary", disabled: !pct || reason.length < 3 || save.isPending, onClick: () => save.mutate(), children: save.isPending ? "Saving…" : "Apply" })] }), save.isError && _jsx(Failed, { error: save.error }), _jsx("p", { className: "footnote", children: "Recorded with your name, the reason and the previous value." })] }))] }));
}
