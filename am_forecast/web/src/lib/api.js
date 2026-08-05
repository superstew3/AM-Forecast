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
let session = { user: "sam", role: "viewer" };
export function setIdentity(user, role) {
    session = { user, role };
}
async function request(path, init) {
    const res = await fetch(path, {
        ...init,
        headers: {
            "Content-Type": "application/json",
            "X-User": session.user,
            "X-Role": session.role,
            ...(init?.headers ?? {}),
        },
    });
    if (!res.ok) {
        const detail = await res.json().catch(() => ({ detail: res.statusText }));
        throw new Error(detail.detail ?? `Request failed (${res.status})`);
    }
    return (await res.json());
}
export const api = {
    session: () => request("/api/session"),
    basePosition: () => request("/api/base-position"),
    reference: () => request("/api/reference"),
    business: (fy) => request(`/api/business?financial_year=${fy}`),
    managers: (params) => request(`/api/managers?${params}`),
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
