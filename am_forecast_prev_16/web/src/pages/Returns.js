import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { YearOptions, usePeriods } from "../lib/usePeriods";
import { api, money, percent } from "../lib/api";
import { CompositionBar } from "../components/charts";
import { DataTable, Failed, GstBanner, Loading, Metric, Notes, Panel } from "../components/ui";
export default function Returns() {
    const { years, currentFy } = usePeriods();
    const [fyPick, setFyPick] = useState(null);
    // Default to the current financial year once the data says what it is.
    const fy = fyPick === null ? currentFy : fyPick;
    const setFy = setFyPick;
    const [pick, setPick] = useState(null);
    const q = useQuery({ queryKey: ["return-analysis", fy], queryFn: () => api.returnAnalysis(fy) });
    if (q.isLoading)
        return _jsx(Loading, { what: "return income" });
    if (q.isError)
        return _jsx(Failed, { error: q.error, retry: () => q.refetch() });
    const d = q.data;
    const items = d.items.map((i) => ({ label: i.classification, value: Number(i.amount) }));
    const rows = pick ? d.items.filter((i) => i.classification === pick) : d.items;
    return (_jsxs(_Fragment, { children: [_jsxs("h1", { children: ["Return income", _jsxs("select", { className: "inline-select", value: fy ?? "", onChange: (e) => setFy(e.target.value ? Number(e.target.value) : undefined), children: [_jsx(YearOptions, { years: years }), _jsx("option", { value: "", children: "All years" })] })] }), _jsx(GstBanner, { meta: d.meta }), _jsx(Notes, { notes: d.meta.notes }), _jsx(Panel, { title: "How much came back out", subtitle: "Return income is money that left again after being earned. It is shown as a positive amount and reduces Net Actual Income.", children: _jsxs("div", { className: "metric-grid", children: [_jsx(Metric, { label: "Positive Actual Income", m: { value: d.positive_income, available: true } }), _jsx(Metric, { label: "Return Income", m: { value: d.total_return_income, available: true }, emphasis: true }), _jsx(Metric, { label: "Net Actual Income", m: { value: d.net_income, available: true }, emphasis: true }), _jsx(Metric, { label: "Return rate", m: { value: d.return_rate, available: d.return_rate !== null }, kind: "percent", hint: "Return income as a share of positive income. The proportion of what you earned that came back out." })] }) }), _jsx(Panel, { title: "What it was made of", subtitle: "Click a category to isolate it.", actions: pick && _jsx("button", { onClick: () => setPick(null), children: "Clear filter" }), children: _jsx(CompositionBar, { items: items, onSelect: (l) => setPick(l === pick ? null : l), selected: pick }) }), _jsx(Panel, { title: pick ? `${pick} detail` : "By category", children: _jsx(DataTable, { caption: "return categories", rows: rows, columns: [
                        { key: "classification", label: "Classification" },
                        { key: "amount", label: "Return income", align: "right",
                            render: (r) => money({ value: r.amount, available: true }) },
                        { key: "share_of_returns", label: "Share of returns", align: "right",
                            render: (r) => percent({ value: r.share_of_returns,
                                available: r.share_of_returns !== null }) },
                        { key: "share_of_positive_income", label: "Of positive income", align: "right",
                            hint: "How much of what was earned this category removed.",
                            render: (r) => percent({ value: r.share_of_positive_income,
                                available: r.share_of_positive_income !== null }) },
                        { key: "transactions", label: "Transactions", align: "right" },
                        { key: "average_per_transaction", label: "Average each", align: "right",
                            render: (r) => money({ value: r.average_per_transaction,
                                available: r.average_per_transaction !== null }) },
                    ] }) })] }));
}
