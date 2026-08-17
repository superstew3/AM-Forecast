import { jsx as _jsx, jsxs as _jsxs, Fragment as _Fragment } from "react/jsx-runtime";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../lib/api";
import { usePeriods } from "../lib/usePeriods";
import { DataTable, Failed, GstBanner, Loading, Panel } from "../components/ui";
/**
 * Settings and reference data.
 *
 * Everything here would otherwise be a code change. New policy classes and
 * manager names arrive with every insurer export, and the reporting cut-off
 * moves every month. Leaving these in the database only would have meant a
 * developer for routine work.
 */
export default function Settings() {
    const qc = useQueryClient();
    const { periods } = usePeriods();
    const mappings = useQuery({ queryKey: ["mappings"], queryFn: api.mappings });
    const [cutOff, setCutOff] = useState("");
    const [cutReason, setCutReason] = useState("");
    const [cls, setCls] = useState({ source_type: "renewals", source_value: "",
        canonical_class: "" });
    const [alias, setAlias] = useState({ source_manager: "", canonical_manager: "" });
    const invalidate = () => {
        qc.invalidateQueries();
    };
    const saveCutOff = useMutation({
        mutationFn: () => api.post("/api/settings/cut-off", { cut_off_date: cutOff, reason: cutReason }),
        onSuccess: () => { invalidate(); setCutReason(""); },
    });
    const saveClass = useMutation({
        mutationFn: (body) => api.post("/api/reference/class-equivalence", body),
        onSuccess: () => {
            invalidate();
            setCls({ ...cls, source_value: "",
                canonical_class: "" });
        },
    });
    const saveAlias = useMutation({
        mutationFn: () => api.post("/api/reference/manager-alias", alias),
        onSuccess: () => {
            invalidate();
            setAlias({ source_manager: "",
                canonical_manager: "" });
        },
    });
    if (mappings.isLoading)
        return _jsx(Loading, { what: "settings" });
    if (mappings.isError)
        return _jsx(Failed, { error: mappings.error, retry: () => mappings.refetch() });
    const m = mappings.data;
    return (_jsxs(_Fragment, { children: [_jsx("h1", { children: "Settings and mappings" }), _jsx(GstBanner, {}), _jsxs("div", { className: "purpose", children: [_jsx("strong", { children: "What this page is for." }), " The things that need maintaining as the business changes: the reporting cut-off, how source manager names map to reporting managers, and how policy classes from the two systems line up. Every change here is recorded with your name and reason, and applies to past periods as well as future ones."] }), _jsxs(Panel, { title: "Reporting cut-off date", subtitle: "The line between completed and future periods. It governs what counts as an actual, what is still Pending, and which months are measured against budget.", children: [_jsxs("div", { className: "form-row", children: [_jsxs("label", { children: ["Current", _jsx("input", { value: periods?.cut_off_date ?? "", disabled: true })] }), _jsxs("label", { children: ["New cut-off", _jsx("input", { type: "date", value: cutOff, onChange: (e) => setCutOff(e.target.value) })] }), _jsxs("label", { className: "grow", children: ["Reason (required)", _jsx("input", { value: cutReason, onChange: (e) => setCutReason(e.target.value), placeholder: "e.g. August sales report loaded and reconciled" })] }), _jsx("button", { disabled: !cutOff || cutReason.length < 3 || saveCutOff.isPending, onClick: () => saveCutOff.mutate(), children: saveCutOff.isPending ? "Saving…" : "Set cut-off" })] }), saveCutOff.isError && _jsx(Failed, { error: saveCutOff.error }), _jsx("p", { className: "footnote", children: "Moving the cut-off backwards past months that already hold transactions is refused. Those months are complete, and pretending otherwise would hide actual income." })] }), _jsxs(Panel, { title: `Policy classes needing a mapping (${m.unmapped_classes.length})`, subtitle: "The two sources use different class vocabularies. An unmapped class still matches on client and policy number, but cannot reach the top matching tier. Mapping them improves match confidence.", children: [_jsxs("div", { className: "form-row", children: [_jsxs("label", { children: ["Source", _jsxs("select", { value: cls.source_type, onChange: (e) => setCls({ ...cls, source_type: e.target.value }), children: [_jsx("option", { value: "renewals", children: "Renewals Pending" }), _jsx("option", { value: "sales", children: "Sales Transactions" })] })] }), _jsxs("label", { children: ["Class value", _jsx("input", { value: cls.source_value, placeholder: "e.g. MARINE HULL", onChange: (e) => setCls({ ...cls, source_value: e.target.value }) })] }), _jsxs("label", { children: ["Maps to canonical class", _jsx("input", { value: cls.canonical_class, placeholder: "e.g. MARINE_HULL", list: "canonical-classes", onChange: (e) => setCls({ ...cls, canonical_class: e.target.value }) }), _jsx("datalist", { id: "canonical-classes", children: m.canonical_classes.map((c) => (_jsx("option", { value: c.canonical_class }, c.canonical_class))) })] }), _jsx("button", { disabled: !cls.source_value || !cls.canonical_class || saveClass.isPending, onClick: () => saveClass.mutate(cls), children: "Add mapping" })] }), saveClass.isError && _jsx(Failed, { error: saveClass.error }), _jsx(DataTable, { caption: "unmapped classes", rows: m.unmapped_classes.slice(0, 40), columns: [
                            { key: "source_type", label: "Source" },
                            { key: "source_value", label: "Class value" },
                            { key: "records", label: "Records", align: "right" },
                            { key: "map", label: "",
                                render: (r) => (_jsx("button", { onClick: () => setCls({ source_type: r.source_type,
                                        source_value: r.source_value,
                                        canonical_class: "" }), children: "Map this" })) },
                        ] })] }), _jsx(Panel, { title: `Unmapped source managers (${m.unmapped_managers.length})`, subtitle: "A source manager with no alias has income that rolls up to nobody. These should be empty.", children: m.unmapped_managers.length === 0 ? (_jsx("div", { className: "state empty", children: "Every source manager maps to a reporting manager." })) : (_jsxs(_Fragment, { children: [_jsxs("div", { className: "form-row", children: [_jsxs("label", { children: ["Source manager", _jsx("input", { value: alias.source_manager, onChange: (e) => setAlias({ ...alias,
                                                source_manager: e.target.value }) })] }), _jsxs("label", { children: ["Reports as", _jsxs("select", { value: alias.canonical_manager, onChange: (e) => setAlias({ ...alias,
                                                canonical_manager: e.target.value }), children: [_jsx("option", { value: "", children: "Choose\u2026" }), [...new Set(m.manager_aliases.map((a) => a.canonical_manager))]
                                                    .map((c) => _jsx("option", { value: c, children: c }, c))] })] }), _jsx("button", { disabled: !alias.source_manager || !alias.canonical_manager, onClick: () => saveAlias.mutate(), children: "Add alias" })] }), saveAlias.isError && _jsx(Failed, { error: saveAlias.error }), _jsx(DataTable, { caption: "unmapped managers", rows: m.unmapped_managers, columns: [
                                { key: "source_manager", label: "Source manager" },
                                { key: "transactions", label: "Transactions", align: "right" },
                            ] })] })) }), _jsx(Panel, { title: "Manager aliases", subtitle: "Applied by join at read time, so a correction fixes actuals, forecasts and budgets together rather than only new records.", children: _jsx(DataTable, { caption: "aliases", rows: m.manager_aliases, columns: [
                        { key: "source_manager", label: "Source name" },
                        { key: "canonical_manager", label: "Reports as" },
                        { key: "status", label: "Status" },
                        { key: "include_in_rankings", label: "In rankings",
                            render: (r) => (r.include_in_rankings ? "Yes" : "No") },
                        { key: "note", label: "Note", render: (r) => r.note ?? "—" },
                    ] }) }), _jsx(Panel, { title: "Exclusion rules", subtitle: "Matching records are imported in full and flagged, never dropped. Deactivating a rule brings its records back into reported totals.", children: _jsx(DataTable, { caption: "exclusion rules", rows: m.exclusion_rules, columns: [
                        { key: "source_type", label: "Source" },
                        { key: "target_field", label: "Field" },
                        { key: "match_type", label: "Match" },
                        { key: "match_value", label: "Value" },
                        { key: "active", label: "Active", render: (r) => (r.active ? "Yes" : "No") },
                        { key: "note", label: "Note", render: (r) => r.note ?? "—" },
                    ] }) }), _jsx(Panel, { title: "Transaction categories", subtitle: "An unknown category is never guessed at: it classifies as Unmapped and appears in Data Quality.", children: _jsx(DataTable, { caption: "categories", rows: m.category_map, columns: [
                        { key: "category", label: "Code" },
                        { key: "business_classification", label: "Business classification" },
                        { key: "description", label: "Meaning" },
                    ] }) })] }));
}
