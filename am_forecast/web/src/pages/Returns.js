import { jsx as _jsx, Fragment as _Fragment, jsxs as _jsxs } from "react/jsx-runtime";
import { useQuery } from "@tanstack/react-query";
import { api, money } from "../lib/api";
import { DataTable, Failed, GstBanner, Loading, Panel } from "../components/ui";
export default function Returns() {
    const params = new URLSearchParams();
    const q = useQuery({ queryKey: ["returns"], queryFn: () => api.returnIncome(params) });
    if (q.isLoading)
        return _jsx(Loading, { what: "return income" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    return (_jsxs(_Fragment, { children: [_jsx("h1", { children: "Return income" }), _jsx(GstBanner, { meta: d.meta }), _jsx(Panel, { title: "Where income was returned", subtitle: "Signed and absolute values are both shown. Signed amounts reduce Net Actual Income; absolute amounts show the size of the leakage.", actions: _jsx("a", { className: "button", href: api.exportUrl("return-income", "csv", params), children: "Export CSV" }), children: _jsx(DataTable, { caption: "return income categories", rows: d.items, serverTotals: {
                        signed_return_income: money({ value: d.total.signed, available: true }),
                        absolute_return_income: money({ value: d.total.absolute, available: true }),
                        transaction_rows: d.total.rows,
                    }, columns: [
                        { key: "derived_classification", label: "Classification" },
                        { key: "signed_return_income", label: "Signed", align: "right",
                            render: (r) => money({ value: r.signed_return_income, available: true }) },
                        { key: "absolute_return_income", label: "Absolute", align: "right",
                            render: (r) => money({ value: r.absolute_return_income, available: true }) },
                        { key: "transaction_rows", label: "Transactions", align: "right" },
                    ] }) })] }));
}
