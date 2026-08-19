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

export type Money = { value: string | number | null; available: boolean; reason?: string | null };
export type Ratio = Money;

export interface Meta {
  cut_off_date: string;
  generated_at: string;
  timezone: string;
  gst_note: string;
  financial_year: number | null;
  notes: string[];
}

export interface BusinessSummary {
  financial_year: number;
  coverage_status: string | null;
  period_label: string | null;
  net_actual_income: Money;
  positive_actual_income: Money;
  return_income: Money;
  original_renewal_forecast: Money;
  latest_renewal_forecast: Money;
  forecast_movement: Money;
  total_budget: Money;
  budget_achievement: Ratio;
  latest_outlook: Money;
  remaining_budget_gap: Money;
  actual_new_business: Money;
  lapse_return_income: Money;
  midterm_cancellation_return_income: Money;
  new_business_cancellation_return_income: Money;
  negative_endorsements: Money;
  endorsement_cancellations: Money;
  meta: Meta;
}

export interface ManagerRow {
  canonical_manager: string;
  status: string;
  include_in_rankings: boolean;
  period: string;
  financial_year: number;
  financial_quarter: number | null;
  period_month: string | null;
  original_forecast: Money;
  latest_forecast: Money;
  positive_actual_income: Money;
  return_income: Money;
  net_actual_income: Money;
  new_business_growth_target: Money;
  total_budget: Money;
  budget_variance: Money;
  budget_achievement: Ratio;
  renewal_achievement: Ratio;
  actual_new_business: Money;
  latest_outlook: Money;
  remaining_budget_gap: Money;
  renewal_income: Money;
  renewal_forecast: Money;
  budget_to_date: Money;
  months_elapsed: number;
  budget_verdict: string;
  over_or_under_pct: Ratio;
  baseline_note: string | null;
  has_started: boolean;
}

export interface Cell {
  month: string;
  value: string | number | null;
  status: "actual" | "future" | "unavailable";
  reason?: string | null;
}

export interface GridRow {
  label: string;
  kind: "transaction" | "total" | "forecast" | "budget" | "prior" | "derived";
  value_kind: "money" | "percent" | "count" | "verdict";
  cells: Cell[];
  total: string | number | null;
  hint?: string | null;
}

export interface ManagerDetail {
  canonical_manager: string;
  status: string;
  include_in_rankings: boolean;
  financial_year: number;
  financial_year_label: string;
  months: string[];
  month_status: string[];
  cut_off_month: string;
  prior_year_actual: Money;
  ytd_actual: Money;
  ytd_budget: Money;
  ytd_achievement: Ratio;
  full_year_budget: Money;
  full_year_original_forecast: Money;
  full_year_latest_forecast: Money;
  latest_outlook: Money;
  remaining_budget_gap: Money;
  forecast_achievement: Ratio;
  active_growth_pct: Ratio;
  active_growth_basis: string | null;
  quarter_growth: {
    financial_quarter: number;
    growth_pct: string | number | null;
    growth_basis: string;
    dollar_override: string | number | null;
  }[];
  rows: GridRow[];
  quarters: any[];
  meta: Meta;
}

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
export function money(m: Money | null | undefined): string {
  if (!m || !m.available || m.value === null || m.value === undefined) return NA;
  const n = typeof m.value === "string" ? Number(m.value) : m.value;
  if (Number.isNaN(n)) return NA;
  return n < 0 ? `(${AUD.format(Math.abs(n))})` : AUD.format(n);
}

/** True when a measure is a real, negative number. Drives the red styling. */
export function isNegative(m: Money | Ratio | null | undefined): boolean {
  if (!m || !m.available || m.value === null || m.value === undefined) return false;
  return Number(m.value) < 0;
}

/** Format a percentage. Never substitutes 0% for unavailable. */
export function percent(r: Ratio | null | undefined, digits = 1): string {
  if (!r || !r.available || r.value === null || r.value === undefined) return NA;
  const n = typeof r.value === "string" ? Number(r.value) : r.value;
  if (Number.isNaN(n)) return NA;
  return `${(n * 100).toFixed(digits)}%`;
}

export function isUnavailable(m: Money | Ratio | null | undefined): boolean {
  return !m || !m.available || m.value === null || m.value === undefined;
}

export function reasonFor(m: Money | Ratio | null | undefined): string | undefined {
  return m && !m.available && m.reason ? m.reason : undefined;
}

/** Favourable / adverse tone. Unavailable measures are neutral, never adverse. */
export function tone(r: Ratio | null | undefined): "good" | "watch" | "bad" | "none" {
  if (isUnavailable(r)) return "none";
  const n = Number(r!.value);
  if (n >= 1) return "good";
  if (n >= 0.95) return "watch";
  return "bad";
}

export function dateAU(value: string | null | undefined): string {
  if (!value) return NA;
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-AU", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "Australia/Melbourne",
  });
}

export function monthAU(value: string | null | undefined): string {
  if (!value) return NA;
  return new Date(`${value}T00:00:00`).toLocaleDateString("en-AU", {
    month: "short",
    year: "numeric",
    timeZone: "Australia/Melbourne",
  });
}

// --- client ------------------------------------------------------------------

export interface FinancialYear {
  financial_year: number;
  label: string;
  has_actuals: boolean;
  has_forecast: boolean;
  is_current: boolean;
  coverage_status: string | null;
  coverage_note: string | null;
}

export interface Periods {
  current_financial_year: number;
  current_financial_year_label: string;
  current_quarter: number;
  cut_off_date: string;
  cut_off_month: string;
  financial_years: FinancialYear[];
  quarters: { quarter: number; label: string; months: string }[];
}

export interface Session {
  username: string;
  role: "viewer" | "manager" | "administrator";
  can: Record<string, boolean>;
}

const IDENTITY_KEY = "am-forecast-identity";

function loadIdentity(): { user: string; role: string } {
  try {
    const raw = localStorage.getItem(IDENTITY_KEY);
    if (raw) return JSON.parse(raw);
  } catch {
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

export function setIdentity(user: string, role: string) {
  session = { user, role };
  try {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify(session));
  } catch {
    // non-fatal: the role simply will not survive a reload
  }
}

export class NotSignedIn extends Error {}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
  return (await res.json()) as T;
}

export const api = {
  session: () => request<Session>("/api/session"),
  login: (email: string, password: string) =>
    request<any>("/api/auth/login",
                 { method: "POST", body: JSON.stringify({ email, password }) }),
  logout: () => request<any>("/api/auth/logout", { method: "POST" }),
  me: () => request<any>("/api/auth/me"),
  periods: () => request<Periods>("/api/periods"),
  mappings: () => request<any>("/api/reference/mappings"),
  basePosition: () => request<any>("/api/base-position"),
  reference: () => request<any>("/api/reference"),
  business: (fy: number) => request<BusinessSummary>(`/api/business?financial_year=${fy}`),
  yearOverYear: (fy: number, manager?: string) =>
    request<any>(`/api/analytics/year-over-year?financial_year=${fy}` +
                 (manager ? `&manager=${encodeURIComponent(manager)}` : "")),
  managerMatrix: (fy: number, measure: string, includeNonRanked: boolean) =>
    request<any>(`/api/analytics/manager-matrix?financial_year=${fy}` +
                 `&measure=${measure}&include_non_ranked=${includeNonRanked}`),
  returnAnalysis: (fy?: number, manager?: string) =>
    request<any>("/api/analytics/return-income" +
                 (fy ? `?financial_year=${fy}` : "?") +
                 (manager ? `&manager=${encodeURIComponent(manager)}` : "")),
  managerDetail: (manager: string, fy: number) =>
    request<ManagerDetail>(
      `/api/managers/${encodeURIComponent(manager)}/detail?financial_year=${fy}`),
  managers: (params: URLSearchParams) =>
    request<{ items: ManagerRow[]; total: number; meta: Meta }>(`/api/managers?${params}`),
  bonus: (fy: number, includeNonRanked = false) =>
    request<any>(`/api/bonus?financial_year=${fy}` +
                 (includeNonRanked ? "&include_non_ranked=true" : "")),
  bonusForManager: (manager: string, fy: number) =>
    request<any>(`/api/bonus/${encodeURIComponent(manager)}?financial_year=${fy}`),
  forecastHistory: (manager: string, fy: number) =>
    request<any>(`/api/forecast-history?manager=${encodeURIComponent(manager)}` +
                 `&financial_year=${fy}`),
  forecastMovement: (params: URLSearchParams) =>
    request<any>(`/api/forecast-movement?${params}`),
  returnIncome: (params: URLSearchParams) => request<any>(`/api/return-income?${params}`),
  newBusiness: (params: URLSearchParams) => request<any>(`/api/new-business?${params}`),
  policies: (params: URLSearchParams) => request<any>(`/api/policies?${params}`),
  review: (kind: string, params: URLSearchParams) =>
    request<any>(`/api/review?kind=${kind}&${params}`),
  reviewHistory: () => request<any>("/api/review/history"),
  dataQuality: () => request<any>("/api/data-quality"),
  dataQualityDetail: (indicator: string) =>
    request<any>(`/api/data-quality/${indicator}`),
  uploads: () => request<any>("/api/uploads"),
  budget: (fy: number) => request<any>(`/api/budget?financial_year=${fy}`),
  budgetAudit: () => request<any>("/api/budget/audit"),
  post: <T>(path: string, body: unknown) =>
    request<T>(path, { method: "POST", body: JSON.stringify(body) }),
  exportUrl: (dataset: string, fmt: string, params: URLSearchParams) =>
    `/api/export/${dataset}?fmt=${fmt}&${params}`,
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
export function cell(
  c: Cell,
  kind: "money" | "percent" | "count" | "verdict" = "money",
): string {
  if (c.status === "future") return NOT_YET;
  if (c.status === "unavailable" || c.value === null) return NA;
  if (kind === "verdict") return Number(c.value) >= 1 ? "YES" : "NO";
  if (kind === "percent") {
    const n = Number(c.value);
    // Above/below rows read better with an explicit sign.
    return n > 0 ? `+${percent({ value: n, available: true })}`
                 : percent({ value: n, available: true });
  }
  if (kind === "count") return String(Math.round(Number(c.value)));
  return money({ value: c.value, available: true });
}

export function cellTitle(c: Cell): string | undefined {
  if (c.status === "future") return "This month has not started yet.";
  if (c.status === "unavailable") return c.reason ?? "Not available";
  return undefined;
}
