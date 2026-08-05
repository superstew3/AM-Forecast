import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, setIdentity, currentIdentity } from "./lib/api";
import Business from "./pages/Business";
import Managers from "./pages/Managers";
import Movement from "./pages/Movement";
import Returns from "./pages/Returns";
import NewBusiness from "./pages/NewBusiness";
import Policies from "./pages/Policies";
import Review from "./pages/Review";
import DataQuality from "./pages/DataQuality";
import Uploads from "./pages/Uploads";
import Budget from "./pages/Budget";
const AREAS = [
    { to: "/business", label: "Business performance" },
    { to: "/managers", label: "Account managers" },
    { to: "/movement", label: "Forecast movement" },
    { to: "/returns", label: "Return income" },
    { to: "/new-business", label: "New business" },
    { to: "/policies", label: "Policy renewals" },
    { to: "/review", label: "Matching review" },
    { to: "/budget", label: "Budget" },
    { to: "/data-quality", label: "Data quality" },
    { to: "/uploads", label: "Uploads & audit" },
];
export default function App() {
    const session = useQuery({ queryKey: ["session"], queryFn: api.session });
    const base = useQuery({ queryKey: ["base"], queryFn: api.basePosition });
    return (_jsxs("div", { className: "shell", children: [_jsxs("aside", { children: [_jsxs("div", { className: "brand", children: [_jsx("span", { className: "brand-mark", children: "AM" }), _jsxs("div", { children: [_jsx("strong", { children: "Income Forecasting" }), _jsx("small", { children: "Performance & budget" })] })] }), _jsx("nav", { children: AREAS.map((a) => (_jsx(NavLink, { to: a.to, className: ({ isActive }) => (isActive ? "active" : ""), children: a.label }, a.to))) }), _jsxs("div", { className: "identity", children: [_jsxs("label", { children: ["Role", _jsxs("select", { defaultValue: currentIdentity().role, onChange: (e) => {
                                            setIdentity("sam", e.target.value);
                                            window.location.reload();
                                        }, children: [_jsx("option", { value: "viewer", children: "Viewer" }), _jsx("option", { value: "manager", children: "Manager" }), _jsx("option", { value: "administrator", children: "Administrator" })] })] }), _jsxs("small", { children: [session.data?.username ?? "…", " \u00B7 ", session.data?.role ?? ""] })] }), base.data && !base.data.is_base_state && (_jsx("div", { className: "warning small", children: "The database is not in the clean base state. Figures on screen may include test data." }))] }), _jsx("main", { children: _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Navigate, { to: "/business", replace: true }) }), _jsx(Route, { path: "/business", element: _jsx(Business, {}) }), _jsx(Route, { path: "/managers", element: _jsx(Managers, {}) }), _jsx(Route, { path: "/movement", element: _jsx(Movement, {}) }), _jsx(Route, { path: "/returns", element: _jsx(Returns, {}) }), _jsx(Route, { path: "/new-business", element: _jsx(NewBusiness, {}) }), _jsx(Route, { path: "/policies", element: _jsx(Policies, {}) }), _jsx(Route, { path: "/review", element: _jsx(Review, {}) }), _jsx(Route, { path: "/budget", element: _jsx(Budget, {}) }), _jsx(Route, { path: "/data-quality", element: _jsx(DataQuality, {}) }), _jsx(Route, { path: "/uploads", element: _jsx(Uploads, {}) })] }) })] }));
}
