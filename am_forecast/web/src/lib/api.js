/**
 * API types and formatting.
 *
 * The single rule this file exists to enforce: an unavailable measure is not
 * zero. The API sends `{ value: null, available: false, reason }` and the
 * interface renders "N/A" with the reason in a tooltip. Rendering $0 or 0%
 * instead would report a manager as having failed when the truth is that we
 * cannot say.
 *
 * Nothing here recalculates a financial figure. Formatting only.
 */
export const NA = "N/A";
/** A month that has not happened yet. Distinct from N/A, which means unknown. */
export const NOT_YET = "\u2014";
export const GST_NOTE = "All income figures are GST inclusive.";
const AUD = new Intl.NumberFormat("en-AU", {
    style: "currency",
    currency: "AUD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
});
/** Format a monetary measure. Never substitutes zero for unavailable. */
export function money(m) {
    if (!m || !m.available || m.value === null || m.value === undefined)
        return NA;
    const n = typeof m.value === "string" ? Number(m.value) : m.value;
    if (Number.isNaN(n))
        return NA;
    return n < 0 ? `(${AUD.format(Math.abs(n))})` : AUD.format(n);
}
/** True when a measure is a real, negative number. Drives the red styling. */
export function isNegative(m) {
    if (!m || !m.available || m.value === null || m.value === undefined)
        return false;
    return Number(m.value) < 0;
}
/** Format a percentage. Never substitutes 0% for unavailable. */
export function percent(r, digits = 1) {
    if (!r || !r.available || r.value === null || r.value === undefined)
        return NA;
    const n = typeof r.value === "string" ? Number(r.value) : r.value;
    if (Number.isNaN(n))
        return NA;
    return `${(n * 100).toFixed(digits)}%`;
}
export function isUnavailable(m) {
    return !m || !m.available || m.value === null || m.value === undefined;
}
export function reasonFor(m) {
    return m && !m.available && m.reason ? m.reason : undefined;
}
/** Favourable / adverse tone. Unavailable measures are neutral, never adverse. */
export function tone(r) {
    if (isUnavailable(r))
        return "none";
    const n = Number(r.value);
    if (n >= 1)
        return "good";
    if (n >= 0.95)
        return "watch";
    return "bad";
}
export function dateAU(value) {
    if (!value)
        return NA;
    return new Date(`${value}T00:00:00`).toLocaleDateString("en-AU", {
        day: "2-digit",
        month: "short",
        year: "numeric",
        timeZone: "Australia/Melbourne",
    });
}
export function monthAU(value) {
    if (!value)
        return NA;
    return new Date(`${value}T00:00:00`).toLocaleDateString("en-AU", {
        month: "short",
        year: "numeric",
        timeZone: "Australia/Melbourne",
    });
}
const IDENTITY_KEY = "am-forecast-identity";
function loadIdentity() {
    try {
        const raw = localStorage.getItem(IDENTITY_KEY);
        if (raw)
            return JSON.parse(raw);
    }
    catch {
        // storage unavailable; fall through to the default
    }
    return { user: "sam", role: "viewer" };
}
/**
 * Identity is held in localStorage, not just in memory.
 *
 * The role selector reloads the page so every query refetches, which wiped an
 * in-memory value before it was ever used and silently reset the role to
 * viewer on every change.
 */
let session = loadIdentity();
export function currentIdentity() {
    return session;
}
export function setIdentity(user, role) {
    session = { user, role };
    try {
        localStorage.setItem(IDENTITY_KEY, JSON.stringify(session));
    }
    catch {
        // non-fatal: the role simply will not survive a reload
    }
}
export class NotSignedIn extends Error {
}
async function request(path, init) {
    const res = await fetch(path, {
        ...init,
        // The session cookie is HttpOnly, so it travels only because of this.
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
    if (res.status === 401) {
        const detail = await res.json().catch(() => ({ detail: "Not signed in." }));
        throw new NotSignedIn(detail.detail ?? "Not signed in.");
    }
    if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(detail.detail ?? `Request failed (${res.status})`);
    }
    return (await res.json());
}
export const api = {
    session: () => request("/api/session"),
    login: (email, password) => request("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
    logout: () => request("/api/auth/logout", { method: "POST" }),
    me: () => request("/api/auth/me"),
    periods: () => request("/api/periods"),
    mappings: () => request("/api/reference/mappings"),
    basePosition: () => request("/api/base-position"),
    reference: () => request("/api/reference"),
    business: (fy) => request(`/api/business?financial_year=${fy}`),
    yearOverYear: (fy, manager) => request(`/api/analytics/year-over-year?financial_year=${fy}` +
        (manager ? `&manager=${encodeURIComponent(manager)}` : "")),
    managerMatrix: (fy, measure, includeNonRanked) => request(`/api/analytics/manager-matrix?financial_year=${fy}` +
        `&measure=${measure}&include_non_ranked=${includeNonRanked}`),
    returnAnalysis: (fy, manager) => request("/api/analytics/return-income" +
        (fy ? `?financial_year=${fy}` : "?") +
        (manager ? `&manager=${encodeURIComponent(manager)}` : "")),
    managerDetail: (manager, fy) => request(`/api/managers/${encodeURIComponent(manager)}/detail?financial_year=${fy}`),
    managers: (params) => request(`/api/managers?${params}`),
    bonus: (fy) => request(`/api/bonus?financial_year=${fy}`),
    bonusForManager: (manager, fy) => request(`/api/bonus/${encodeURIComponent(manager)}?financial_year=${fy}`),
    forecastHistory: (manager, fy) => request(`/api/forecast-history?manager=${encodeURIComponent(manager)}` +
        `&financial_year=${fy}`),
    forecastMovement: (params) => request(`/api/forecast-movement?${params}`),
    returnIncome: (params) => request(`/api/return-income?${params}`),
    newBusiness: (params) => request(`/api/new-business?${params}`),
    policies: (params) => request(`/api/policies?${params}`),
    review: (kind, params) => request(`/api/review?kind=${kind}&${params}`),
    reviewHistory: () => request("/api/review/history"),
    dataQuality: () => request("/api/data-quality"),
    dataQualityDetail: (indicator) => request(`/api/data-quality/${indicator}`),
    uploads: () => request("/api/uploads"),
    budget: (fy) => request(`/api/budget?financial_year=${fy}`),
    budgetAudit: () => request("/api/budget/audit"),
    post: (path, body) => request(path, { method: "POST", body: JSON.stringify(body) }),
    exportUrl: (dataset, fmt, params) => `/api/export/${dataset}?fmt=${fmt}&${params}`,
};
/**
 * Format a grid cell.
 *
 * Three outcomes, deliberately distinct:
 *   actual      -> the number
 *   future      -> an em dash; the month has not happened
 *   unavailable -> N/A; we cannot say
 *
 * Showing a future month as N/A made the manager screen look broken when it was
 * merely early in the financial year.
 */
export function cell(c, kind = "money") {
    if (c.status === "future")
        return NOT_YET;
    if (c.status === "unavailable" || c.value === null)
        return NA;
    if (kind === "verdict")
        return Number(c.value) >= 1 ? "YES" : "NO";
    if (kind === "percent") {
        const n = Number(c.value);
        // Above/below rows read better with an explicit sign.
        return n > 0 ? `+${percent({ value: n, available: true })}`
            : percent({ value: n, available: true });
    }
    if (kind === "count")
        return String(Math.round(Number(c.value)));
    return money({ value: c.value, available: true });
}
export function cellTitle(c) {
    if (c.status === "future")
        return "This month has not started yet.";
    if (c.status === "unavailable")
        return c.reason ?? "Not available";
    return undefined;
}
