import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, setIdentity } from "./lib/api";
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

  return (
    <div className="shell">
      <aside>
        <div className="brand">
          <span className="brand-mark">AM</span>
          <div>
            <strong>Income Forecasting</strong>
            <small>Performance &amp; budget</small>
          </div>
        </div>
        <nav>
          {AREAS.map((a) => (
            <NavLink key={a.to} to={a.to}
                     className={({ isActive }) => (isActive ? "active" : "")}>
              {a.label}
            </NavLink>
          ))}
        </nav>
        <div className="identity">
          <label>
            Role
            <select defaultValue="viewer"
                    onChange={(e) => { setIdentity("sam", e.target.value);
                                       window.location.reload(); }}>
              <option value="viewer">Viewer</option>
              <option value="manager">Manager</option>
              <option value="administrator">Administrator</option>
            </select>
          </label>
          <small>{session.data?.username ?? "…"} &middot; {session.data?.role ?? ""}</small>
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
          <Route path="/managers" element={<Managers />} />
          <Route path="/movement" element={<Movement />} />
          <Route path="/returns" element={<Returns />} />
          <Route path="/new-business" element={<NewBusiness />} />
          <Route path="/policies" element={<Policies />} />
          <Route path="/review" element={<Review />} />
          <Route path="/budget" element={<Budget />} />
          <Route path="/data-quality" element={<DataQuality />} />
          <Route path="/uploads" element={<Uploads />} />
        </Routes>
      </main>
    </div>
  );
}
