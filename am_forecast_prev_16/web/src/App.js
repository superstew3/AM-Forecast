import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { NotSignedIn, api } from "./lib/api";
import ChangePassword from "./pages/ChangePassword";
import Login from "./pages/Login";
import Business from "./pages/Business";
import AllManagers from "./pages/AllManagers";
import ManagerDetail from "./pages/ManagerDetail";
import ManagerIndex from "./pages/ManagerIndex";
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
    { to: "/managers-index", label: "Account managers" },
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
    const [user, setUser] = useState(null);
    const [checking, setChecking] = useState(true);
    // Ask the server who we are. The cookie is HttpOnly, so this is the only way
    // the interface can know whether a session exists.
    useEffect(() => {
        api.me()
            .then(setUser)
            .catch((e) => { if (!(e instanceof NotSignedIn))
            console.error(e); })
            .finally(() => setChecking(false));
    }, []);
    if (checking)
        return _jsx("div", { className: "login-shell", children: _jsx("div", { className: "state loading", children: "Loading\u2026" }) });
    if (!user)
        return _jsx(Login, { onSignedIn: setUser });
    if (user.must_change_password) {
        return _jsx(ChangePassword, { displayName: user.display_name, onDone: () => api.me().then(setUser) });
    }
    return _jsx(Shell, { user: user, onSignOut: () => api.logout().then(() => setUser(null)) });
}
function Shell({ user, onSignOut }) {
    const base = useQuery({ queryKey: ["base"], queryFn: api.basePosition });
    return (_jsxs("div", { className: "shell", children: [_jsxs("aside", { children: [_jsxs("div", { className: "brand", children: [_jsx("img", { className: "brand-logo", src: "/broker-plus-logo.png", alt: "Broker+", width: 38, height: 38 }), _jsxs("div", { children: [_jsx("strong", { children: "Income Forecasting" }), _jsx("small", { children: "Performance & budget" })] })] }), _jsx("nav", { children: AREAS.map((a) => (_jsx(NavLink, { to: a.to, end: true, className: ({ isActive }) => (isActive ? "active" : ""), children: a.label }, a.to))) }), _jsxs("div", { className: "identity", children: [_jsx("div", { className: "identity-name", children: user.display_name }), _jsxs("div", { className: "identity-role", children: [user.email, _jsx("span", { className: `role-chip role-${user.role}`, children: user.role })] }), _jsx("button", { className: "signout", onClick: onSignOut, children: "Sign out" })] }), base.data && !base.data.is_base_state && (_jsx("div", { className: "warning small", children: "The database is not in the clean base state. Figures on screen may include test data." }))] }), _jsx("main", { children: _jsxs(Routes, { children: [_jsx(Route, { path: "/", element: _jsx(Navigate, { to: "/business", replace: true }) }), _jsx(Route, { path: "/business", element: _jsx(Business, {}) }), _jsx(Route, { path: "/managers-index", element: _jsx(ManagerIndex, {}) }), _jsx(Route, { path: "/manager", element: _jsx(ManagerDetail, {}) }), _jsx(Route, { path: "/all-managers", element: _jsx(AllManagers, {}) }), _jsx(Route, { path: "/managers", element: _jsx(Managers, {}) }), _jsx(Route, { path: "/forecast-history", element: _jsx(ForecastHistory, {}) }), _jsx(Route, { path: "/returns", element: _jsx(Returns, {}) }), _jsx(Route, { path: "/new-business", element: _jsx(NewBusiness, {}) }), _jsx(Route, { path: "/policies", element: _jsx(Policies, {}) }), _jsx(Route, { path: "/review", element: _jsx(Review, {}) }), _jsx(Route, { path: "/budget", element: _jsx(Budget, {}) }), _jsx(Route, { path: "/bonus", element: _jsx(Bonus, {}) }), _jsx(Route, { path: "/data-quality", element: _jsx(DataQuality, {}) }), _jsx(Route, { path: "/uploads", element: _jsx(Uploads, {}) }), _jsx(Route, { path: "/settings", element: _jsx(Settings, {}) })] }) })] }));
}
