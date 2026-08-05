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
  retention_by_income: Ratio;
  retention_by_policy_count: Ratio;
  baseline_note: string | null;
}

export const NA = "N/A";
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

export interface Session {
  username: string;
  role: "viewer" | "manager" | "administrator";
  can: Record<string, boolean>;
}

let session: { user: string; role: string } = { user: "sam", role: "viewer" };

export function setIdentity(user: string, role: string) {
  session = { user, role };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
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
  return (await res.json()) as T;
}

export const api = {
  session: () => request<Session>("/api/session"),
  basePosition: () => request<any>("/api/base-position"),
  reference: () => request<any>("/api/reference"),
  business: (fy: number) => request<BusinessSummary>(`/api/business?financial_year=${fy}`),
  managers: (params: URLSearchParams) =>
    request<{ items: ManagerRow[]; total: number; meta: Meta }>(`/api/managers?${params}`),
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
