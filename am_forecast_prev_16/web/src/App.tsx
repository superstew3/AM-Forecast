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
  const [user, setUser] = useState<any | null>(null);
  const [checking, setChecking] = useState(true);

  // Ask the server who we are. The cookie is HttpOnly, so this is the only way
  // the interface can know whether a session exists.
  useEffect(() => {
    api.me()
      .then(setUser)
      .catch((e) => { if (!(e instanceof NotSignedIn)) console.error(e); })
      .finally(() => setChecking(false));
  }, []);

  if (checking) return <div className="login-shell"><div className="state loading">Loading…</div></div>;
  if (!user) return <Login onSignedIn={setUser} />;
  if (user.must_change_password) {
    return <ChangePassword displayName={user.display_name}
                           onDone={() => api.me().then(setUser)} />;
  }
  return <Shell user={user} onSignOut={() => api.logout().then(() => setUser(null))} />;
}

function Shell({ user, onSignOut }: { user: any; onSignOut: () => void }) {
  const base = useQuery({ queryKey: ["base"], queryFn: api.basePosition });

  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <img className="brand-logo" src="/broker-plus-logo.png"
               alt="Broker+" width={38} height={38} />
          <div>
            <strong>Income Forecasting</strong>
            <small>Performance &amp; budget</small>
          </div>
        </div>
        <nav>
          {AREAS.map((a) => (
            <NavLink key={a.to} to={a.to} end
                     className={({ isActive }) => (isActive ? "active" : "")}>
              {a.label}
            </NavLink>
          ))}
        </nav>
        <div className="identity">
          <div className="identity-name">{user.display_name}</div>
          <div className="identity-role">
            {user.email}
            <span className={`role-chip role-${user.role}`}>{user.role}</span>
          </div>
          <button className="signout" onClick={onSignOut}>Sign out</button>
        </div>
        {base.data && !base.data.is_base_state && (
          <div className="warning small">
            The database is not in the clean base state. Figures on screen may
            include test data.
          </div>
        )}
      </aside>
      <main>
        <Routes>
          <Route path="/" element={<Navigate to="/business" replace />} />
          <Route path="/business" element={<Business />} />
          <Route path="/managers-index" element={<ManagerIndex />} />
          <Route path="/manager" element={<ManagerDetail />} />
          <Route path="/all-managers" element={<AllManagers />} />
          <Route path="/managers" element={<Managers />} />
          <Route path="/forecast-history" element={<ForecastHistory />} />
          <Route path="/returns" element={<Returns />} />
          <Route path="/new-business" element={<NewBusiness />} />
          <Route path="/policies" element={<Policies />} />
          <Route path="/review" element={<Review />} />
          <Route path="/budget" element={<Budget />} />
          <Route path="/bonus" element={<Bonus />} />
          <Route path="/data-quality" element={<DataQuality />} />
          <Route path="/uploads" element={<Uploads />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
