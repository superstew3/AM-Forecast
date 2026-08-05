import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { api, money } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Notes, Panel, Value } from "../components/ui";
export default function NewBusiness() {
    const params = new URLSearchParams();
    const q = useQuery({ queryKey: ["newbusiness"], queryFn: () => api.newBusiness(params) });
    if (q.isLoading)
        return _jsx(Loading, { what: "new business" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["New business ", _jsx("span", { className: "fy", children: "FY2026-27" })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Notes, { notes: d.meta.notes }), _jsx(Panel, { title: "New business against growth target", subtitle: "The growth target is a budget only. It is never added to Original Forecast, Latest Forecast or Latest Outlook.", children: _jsx(DataTable, { caption: "new business", rows: d.items, columns: [
                        { key: "canonical_manager", label: "Manager" },
                        { key: "financial_quarter", label: "Qtr",
                            render: (r) => `Q${r.financial_quarter}` },
                        { key: "gross_new_business", label: "Positive NB", align: "right",
                            render: (r) => money({ value: r.gross_new_business, available: true }) },
                        { key: "negative_new_business_corrections", label: "NB corrections", align: "right",
                            render: (r) => money({ value: r.negative_new_business_corrections, available: true }) },
                        { key: "new_business_cancellations", label: "Cancelled NB", align: "right",
                            render: (r) => money({ value: r.new_business_cancellations, available: true }) },
                        { key: "net_new_business", label: "Net NB", align: "right",
                            render: (r) => money({ value: r.net_new_business, available: true }) },
                        { key: "new_business_growth_target", label: "Growth target", align: "right",
                            render: (r) => money({ value: r.new_business_growth_target, available: true }) },
                        { key: "growth_target_achievement", label: "Achievement", align: "right",
                            render: (r) => _jsx(Value, { m: r.growth_target_achievement, kind: "percent" }) },
                    ] }) })] }));
}
