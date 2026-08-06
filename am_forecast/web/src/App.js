import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, currentIdentity, setIdentity } from "./lib/api";
import Business from "./pages/Business";
import AllManagers from "./pages/AllManagers";
import ManagerDetail from "./pages/ManagerDetail";
import Managers from "./pages/Managers";
import ForecastHistory from "./pages/ForecastHistory";
import Returns from "./pages/Returns";
import NewBusiness from "./pages/NewBusiness";
import Policies from "./pages/Policies";
import Review from "./pages/Review";
import DataQuality from "./pages/DataQuality";
import Settings from "./pages/Settings";
import Uploads from "./pages/Uploads";
import Bonus from "./pages/Bonus";
import Budget from "./pages/Budget";
const AREAS = [
    { to: "/business", label: "Business performance" },
    { to: "/manager", label: "Account manager" },
    { to: "/all-managers", label: "All managers by month" },
    { to: "/managers", label: "Compare managers" },
    { to: "/forecast-history", label: "Forecast history" },
    { to: "/returns", label: "Return income" },
    { to: "/new-business", label: "New business" },
    { to: "/policies", label: "Policy renewals" },
    { to: "/review", label: "Matching review" },
    { to: "/budget", label: "Budget" },
    { to: "/bonus", label: "Bonus tracker" },
    { to: "/data-quality", label: "Data quality" },
    { to: "/uploads", label: "Uploads & audit" },
    { to: "/settings", label: "Settings & mappings" },
];
export default function App() {
    const session = useQuery({ queryKey: ["session"], queryFn: api.session });
    const base = useQuery({ queryKey: ["base"], queryFn: api.basePosition });
    return (_jsxs("div", { className: "shell", children: [_jsxs("aside", { children: [_jsxs("div", { className: "brand", children: [_jsx("img", { className: "brand-logo", src: "/broker-plus-logo.png", alt: "Broker+", width: 38, height: 38 }), _jsxs("div", { children: [_jsx("strong", { children: "Income Forecasting" }), _jsx("small", { children: "Performance & budget" })] })] }), _jsx("nav", { children: AREAS.map((a) => (_jsx(NavLink, { to: a.to, end: true, className: ({ isActive }) => (isActive ? "active" : ""), children: a.label }, a.to))) }), _jsxs("div", { className: "identity", children: [_jsxs("label", { children: ["Role", _jsxs("select", { defaultValue: currentIdentity().role, onChange: (e) => {
                                            setIdentity("sam", e.target.value);
                                            window.location.reload();
                                        }, children: [_jsx("option", { value: "viewer", children: "Viewer" }), _jsx("option", { value: "manager", children: "Manager" }), _jsx("option", { value: "administrator", children: "Administrator" })] })] }), _jsxs("small", { children: [session.data?.username ?? "…", " \u00B7 ", session.data?.role ?? ""] })] }), base.data && !base.data.is_base_state && (_jsx("div", { className: "warning small", children: "The database is not in the clean base state. Figures on screen may include test data." }))] }), _jsx("main", { children: _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Navigate, { to: "/business", replace: true }) }), _jsx(Route, { path: "/business", element: _jsx(Business, {}) }), _jsx(Route, { path: "/manager", element: _jsx(ManagerDetail, {}) }), _jsx(Route, { path: "/all-managers", element: _jsx(AllManagers, {}) }), _jsx(Route, { path: "/managers", element: _jsx(Managers, {}) }), _jsx(Route, { path: "/forecast-history", element: _jsx(ForecastHistory, {}) }), _jsx(Route, { path: "/returns", element: _jsx(Returns, {}) }), _jsx(Route, { path: "/new-business", element: _jsx(NewBusiness, {}) }), _jsx(Route, { path: "/policies", element: _jsx(Policies, {}) }), _jsx(Route, { path: "/review", element: _jsx(Review, {}) }), _jsx(Route, { path: "/budget", element: _jsx(Budget, {}) }), _jsx(Route, { path: "/bonus", element: _jsx(Bonus, {}) }), _jsx(Route, { path: "/data-quality", element: _jsx(DataQuality, {}) }), _jsx(Route, { path: "/uploads", element: _jsx(Uploads, {}) }), _jsx(Route, { path: "/settings", element: _jsx(Settings, {}) })] }) })] }));
}
