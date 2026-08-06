import { jsx as _jsx, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";
/**
 * Financial years, straight from the data.
 *
 * Every year selector and label in the interface comes from here. Hardcoding
 * FY2026-27 would have meant editing the app each July — the kind of change
 * that does not get made, leaving a screen quietly showing last year.
 */
export function usePeriods() {
    const q = useQuery({
        queryKey: ["periods"],
        queryFn: api.periods,
        staleTime: 5 * 60 * 1000,
    });
    const years = q.data?.financial_years ?? [];
    return {
        ...q,
        periods: q.data,
        years,
        currentFy: q.data?.current_financial_year,
        label: (fy) => years.find((y) => y.financial_year === fy)?.label ??
            `FY${fy}-${String(fy + 1).slice(2)}`,
    };
}
/** Options for a year <select>, newest first. */
export function YearOptions({ years }) {
    return (_jsx(_Fragment, { children: years.map((y) => (_jsx("option", { value: y.financial_year, children: y.label }, y.financial_year))) }));
}
