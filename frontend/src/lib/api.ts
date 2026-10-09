// Typed fetch client for the MarketEdge AI API.
//
// Auth model:
//  - The access token lives only in memory (this module + React context). It is never written to storage.
//  - The refresh token is an httpOnly, SameSite=Strict cookie scoped to /api/v1/auth, set by the backend.
//    Because the browser calls same-origin /api/v1/* (proxied by next.config rewrites) the cookie is sent.
//  - On a 401 we refresh once (single-flight, serialised across tabs with the Web Locks API so two tabs
//    never present the same rotating refresh token — the backend treats reuse as theft) and retry.
import type {
  AdminHealth,
  AlertIn,
  AlertItem,
  AlertKind,
  Diagnostics,
  IdeaHistory,
  JournalIn,
  LiveQuotes,
  NotificationItem,
  NotificationSettings,
  OrderIn,
  PaperPortfolio,
  TelegramLink,
  AdminUser,
  Analysis,
  AuditLog,
  Breadth,
  Candles,
  EngineSettings,
  Job,
  JobStart,
  MarketInfo,
  OptionChain,
  OptionSignals,
  OptionStrategies,
  OptionsOverview,
  Overview,
  PayoffLegIn,
  PayoffOut,
  PositionSizeIn,
  PositionSizeOut,
  Providers,
  Regime,
  Sectors,
  AdminStrategies,
  SetupDetail,
  SignalList,
  SortKey,
  StockDetail,
  StockList,
  TokenOut,
  TopSetups,
  TotpSetup,
  User,
  Watchlist,
} from "./types";

export const API_BASE = "/api/v1";

export class ApiError extends Error {
  status: number;
  headers: Headers | null;
  body: unknown;
  constructor(status: number, message: string, headers: Headers | null = null, body: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.headers = headers;
    this.body = body;
  }
}

let accessToken: string | null = null;
let onAuthLost: (() => void) | null = null;
let refreshInFlight: Promise<string | null> | null = null;

export function setAccessToken(t: string | null) {
  accessToken = t;
}
export function getAccessToken() {
  return accessToken;
}
export function setOnAuthLost(fn: (() => void) | null) {
  onAuthLost = fn;
}

function detailOf(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d)) {
      return d
        .map((e) => {
          if (e && typeof e === "object") {
            const o = e as { msg?: string; loc?: unknown[] };
            const field = Array.isArray(o.loc) ? o.loc.filter((x) => x !== "body").join(".") : "";
            return field ? `${field}: ${o.msg ?? ""}` : (o.msg ?? "");
          }
          return String(e);
        })
        .join("; ");
    }
  }
  return fallback;
}

async function parse(res: Response): Promise<unknown> {
  if (res.status === 204) return null;
  const text = await res.text();
  if (!text) return null;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

async function doRefresh(): Promise<string | null> {
  const run = async () => {
    let res = await fetch(`${API_BASE}/auth/refresh`, { method: "POST", credentials: "same-origin", cache: "no-store" });
    if (res.status === 409) {
      // Another tab rotated the token a moment ago; the browser now holds the new cookie.
      await new Promise((r) => setTimeout(r, 300));
      res = await fetch(`${API_BASE}/auth/refresh`, { method: "POST", credentials: "same-origin", cache: "no-store" });
    }
    if (!res.ok) return null;
    const body = (await res.json()) as TokenOut;
    accessToken = body.access_token;
    return accessToken;
  };
  const locks = typeof navigator !== "undefined" ? (navigator as Navigator & { locks?: LockManager }).locks : undefined;
  try {
    return locks ? await locks.request("marketedge-refresh", run) : await run();
  } catch {
    return null;
  }
}

/** Single-flight refresh. Resolves to the new access token or null when there is no valid session. */
export function refreshSession(): Promise<string | null> {
  if (!refreshInFlight) {
    refreshInFlight = doRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

type Query = Record<string, string | number | boolean | null | undefined>;
interface ReqOpts {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  auth?: boolean;
  signal?: AbortSignal;
}

function qs(q?: Query): string {
  if (!q) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
}

export async function request<T>(path: string, opts: ReqOpts = {}): Promise<T> {
  const { method = "GET", body, query, auth = true, signal } = opts;
  const url = `${API_BASE}${path}${qs(query)}`;
  const send = () => {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth && accessToken) headers.Authorization = `Bearer ${accessToken}`;
    return fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      credentials: "same-origin",
      cache: "no-store",
      signal,
    });
  };
  let res = await send();
  if (res.status === 401 && auth) {
    const t = await refreshSession();
    if (t) res = await send();
    if (res.status === 401) {
      accessToken = null;
      onAuthLost?.();
    }
  }
  const data = await parse(res);
  if (!res.ok) {
    const fallback =
      res.status === 403
        ? "You do not have permission to view this."
        : res.status === 429
          ? "Rate limit exceeded. Try again shortly."
          : `Request failed (${res.status})`;
    throw new ApiError(res.status, detailOf(data, fallback), res.headers, data);
  }
  return data as T;
}

// ---------------------------------------------------------------- endpoints
export const api = {
  authConfig: () => request<{ registration_open: boolean }>("/auth/config", { auth: false }),
  auth: {
    async login(email: string, password: string, totp_code?: string): Promise<TokenOut> {
      const res = await fetch(`${API_BASE}/auth/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({ email, password, ...(totp_code ? { totp_code } : {}) }),
        credentials: "same-origin",
        cache: "no-store",
      });
      const data = await parse(res);
      if (!res.ok) throw new ApiError(res.status, detailOf(data, `Login failed (${res.status})`), res.headers, data);
      const tok = data as TokenOut;
      accessToken = tok.access_token;
      return tok;
    },
    register: (email: string, password: string, full_name: string) =>
      request<User>("/auth/register", { method: "POST", body: { email, password, full_name }, auth: false }),
    logout: async () => {
      try {
        await request<null>("/auth/logout", { method: "POST", auth: false });
      } finally {
        accessToken = null;
      }
    },
    me: () => request<User>("/auth/me"),
    totpSetup: () => request<TotpSetup>("/auth/2fa/setup", { method: "POST" }),
    totpEnable: (code: string) => request<{ totp_enabled: boolean }>("/auth/2fa/enable", { method: "POST", body: { code } }),
    totpDisable: (code: string, password: string) =>
      request<{ totp_enabled: boolean }>("/auth/2fa/disable", { method: "POST", body: { code, password } }),
  },
  markets: {
    list: () => request<{ items: MarketInfo[]; default_market?: string }>("/markets"),
    overview: (market?: string) => request<Overview>("/markets/overview", { query: { market } }),
    regime: (history = 90, market?: string) => request<{ current: Regime; history: { as_of: string; regime: string; volatility: string }[] }>("/markets/regime", { query: { history, market } }),
    breadth: (market?: string) => request<Breadth>("/markets/breadth", { query: { market } }),
    sectors: (market?: string) => request<Sectors>("/markets/sectors", { query: { market } }),
    diagnostics: (market?: string) => request<Diagnostics>("/markets/diagnostics", { query: { market } }),
  },
  signals: {
    top: (q: { sort?: SortKey; direction?: string; limit?: number; market?: string } = {}) => request<TopSetups>("/signals/top", { query: q }),
    list: (q: { status?: "VALID" | "NO_TRADE"; sort?: SortKey; direction?: string; strategy?: string; min_score?: number; market?: string } = {}) =>
      request<SignalList>("/signals", { query: q }),
    get: (id: number | string) => request<SetupDetail>(`/signals/${id}`),
    history: (market?: "NSE" | "CRYPTO" | "NFO") => request<IdeaHistory>("/signals/history", { query: { market } }),
    liveQuotes: (items: string[]) => request<LiveQuotes>("/signals/live-quotes", { query: { items: items.join(",") } }),
  },
  stocks: {
    list: (q: { q?: string; sector?: string; page?: number; page_size?: number; include_indices?: boolean; market?: string } = {}) =>
      request<StockList>("/stocks", { query: q }),
    get: (symbol: string) => request<StockDetail>(`/stocks/${encodeURIComponent(symbol)}`),
    candles: (symbol: string, interval: "1d" | "1w", limit: number) =>
      request<Candles>(`/stocks/${encodeURIComponent(symbol)}/candles`, { query: { interval, limit } }),
    analysis: (symbol: string, mode: string) => request<Analysis>(`/stocks/${encodeURIComponent(symbol)}/analysis`, { query: { mode } }),
  },
  watchlists: {
    list: () => request<{ items: Watchlist[] }>("/watchlists"),
    create: (name: string, market = "NSE") => request<Watchlist>("/watchlists", { method: "POST", body: { name, market } }),
    remove: (id: number) => request<null>(`/watchlists/${id}`, { method: "DELETE" }),
    addItem: (id: number, symbol: string, tags: string[] = [], note = "") =>
      request<Watchlist>(`/watchlists/${id}/items`, { method: "POST", body: { symbol, tags, note } }),
    patchItem: (id: number, itemId: number, patch: { tags?: string[]; note?: string }) =>
      request<Watchlist>(`/watchlists/${id}/items/${itemId}`, { method: "PATCH", body: patch }),
    removeItem: (id: number, itemId: number) => request<null>(`/watchlists/${id}/items/${itemId}`, { method: "DELETE" }),
  },
  options: {
    nifty: () => request<OptionsOverview>("/options/nifty"),
    expiries: () => request<{ expiries: string[]; as_of: string }>("/options/nifty/expiries"),
    chain: (expiry?: string, width = 20) => request<OptionChain>("/options/nifty/chain", { query: { expiry, width } }),
    signals: () => request<OptionSignals>("/options/signals"),
    strategies: () => request<OptionStrategies>("/options/strategies"),
    payoff: (legs: PayoffLegIn[]) => request<PayoffOut>("/options/payoff", { method: "POST", body: { legs } }),
  },
  portfolios: {
    list: () => request<{ items: PaperPortfolio[]; disclaimer: string }>("/portfolios"),
    get: (id: number) => request<PaperPortfolio>(`/portfolios/${id}`),
    create: (name: string, kind: "paper" | "journal", starting_capital: number, base_currency: "INR" | "USD" = "INR") =>
      request<PaperPortfolio>("/portfolios", { method: "POST", body: { name, kind, starting_capital, base_currency } }),
    remove: (id: number) => request<null>(`/portfolios/${id}`, { method: "DELETE" }),
    order: (id: number, body: OrderIn) => request<{ trade_id: number; status: string; note: string }>(`/portfolios/${id}/orders`, { method: "POST", body }),
    fromSetup: (id: number, signalId: number, quantity: number) =>
      request<{ trade_id: number; status: string; note: string }>(`/portfolios/${id}/orders/from-setup/${signalId}`, { method: "POST", query: { quantity } }),
    journal: (id: number, body: JournalIn) => request<{ trade_id: number; status: string }>(`/portfolios/${id}/journal`, { method: "POST", body }),
    patchTrade: (id: number, tid: number, body: { stop?: number; target1?: number; target2?: number; notes?: string }) =>
      request<{ trade_id: number; status: string }>(`/portfolios/${id}/trades/${tid}`, { method: "PATCH", body }),
    closeTrade: (id: number, tid: number) => request<{ trade_id: number; status: string; note: string }>(`/portfolios/${id}/trades/${tid}/close`, { method: "POST" }),
  },
  alerts: {
    kinds: () => request<{ kinds: AlertKind[]; channels: Record<string, { configured: boolean }>; evaluation: string }>("/alerts/kinds"),
    list: () => request<{ items: AlertItem[] }>("/alerts"),
    create: (body: AlertIn) => request<AlertItem>("/alerts", { method: "POST", body }),
    patch: (id: number, body: Partial<{ status: "active" | "paused"; channels: string[]; repeat: boolean; cooldown_minutes: number; note: string }>) =>
      request<AlertItem>(`/alerts/${id}`, { method: "PATCH", body }),
    remove: (id: number) => request<null>(`/alerts/${id}`, { method: "DELETE" }),
    fromSetup: (signalId: number, kinds: string[], channels: string[]) => request<{ items: AlertItem[] }>(`/alerts/from-setup/${signalId}`, { method: "POST", body: { kinds, channels } }),
  },
  notifications: {
    list: (q: { unread?: boolean; limit?: number } = {}) => request<{ items: NotificationItem[]; unread: number }>("/notifications", { query: q }),
    read: (ids?: number[]) => request<{ ok: boolean }>("/notifications/read", { method: "POST", body: ids ?? [] }),
    settings: () => request<NotificationSettings>("/notifications/settings"),
    saveSettings: (body: Partial<{ email_enabled: boolean; default_channels: string[]; whatsapp_number: string; whatsapp_opt_in: boolean; quiet_start_hour: number; quiet_end_hour: number }>) =>
      request<NotificationSettings>("/notifications/settings", { method: "PUT", body }),
    test: () => request<NotificationItem>("/notifications/test", { method: "POST" }),
    telegramLink: () => request<TelegramLink>("/notifications/telegram/link", { method: "POST" }),
    telegramUnlink: () => request<null>("/notifications/telegram/link", { method: "DELETE" }),
    pushSubscribe: (sub: { endpoint: string; keys: Record<string, string> }) => request<{ subscriptions: number }>("/notifications/push/subscribe", { method: "POST", body: sub }),
    pushUnsubscribe: (endpoint: string) => request<null>("/notifications/push/subscribe", { method: "DELETE", query: { endpoint } }),
  },
  risk: {
    positionSize: (body: PositionSizeIn) => request<PositionSizeOut>("/risk/position-size", { method: "POST", body }),
  },
  upstox: {
    status: () =>
      request<{
        mode: "analytics" | "daily" | null;
        rejected_at?: string | null;
        configured: boolean;
        connected: boolean;
        expires_at: string | null;
        redirect_uri: string;
        used_for: { market_data: boolean; options: boolean };
        note: string;
      }>("/upstox/status"),
    connect: () => request<{ url: string }>("/upstox/connect", { method: "POST" }),
    saveAnalyticsToken: (token: string) => request<{ stored: boolean; expires_at: string }>("/upstox/analytics-token", { method: "PUT", body: { token } }),
  },
  admin: {
    health: () => request<AdminHealth>("/admin/health"),
    getEngine: () => request<EngineSettings>("/admin/settings/engine"),
    putEngine: (body: { weights?: Record<string, number>; labels?: [number, string][] }) =>
      request<EngineSettings>("/admin/settings/engine", { method: "PUT", body }),
    providers: () => request<Providers>("/admin/providers"),
    addKey: (provider: string, name: string, value: string) =>
      request<{ id: number; provider: string; name: string }>("/admin/providers/keys", { method: "POST", body: { provider, name, value } }),
    ingest: (full = false, market = "NSE") => request<JobStart>("/admin/jobs/ingest", { method: "POST", query: { full, market } }),
    scan: (market = "NSE") => request<JobStart>("/admin/jobs/scan", { method: "POST", query: { market } }),
    options: () => request<JobStart>("/admin/jobs/options", { method: "POST" }),
    /** The whole end-of-day pipeline for one market (ingest → scan → outcomes …). */
    runDaily: (market: "NSE" | "CRYPTO", full = false) => request<JobStart>("/admin/jobs/daily", { method: "POST", query: { market, full } }),
    strategies: (market: "NSE" | "CRYPTO") => request<AdminStrategies>("/admin/strategies", { query: { market } }),
    setStrategyEnabled: (market: string, id: string, enabled: boolean, reason = "") =>
      request<{ enabled: boolean; note?: string }>(`/admin/strategy-controls/${encodeURIComponent(market)}/${encodeURIComponent(id)}`, {
        method: "PUT",
        body: { enabled, reason },
      }),
    jobs: (limit = 50) => request<{ items: Job[] }>("/admin/jobs", { query: { limit } }),
    users: (page = 1) => request<{ items: AdminUser[] }>("/admin/users", { query: { page } }),
    changeUser: (id: number, role: string, is_active?: boolean) =>
      request<{ id: number; role: string; is_active: boolean }>(`/admin/users/${id}`, { method: "PATCH", body: { role, is_active } }),
    audit: (limit = 100, action?: string) => request<{ items: AuditLog[] }>("/admin/audit-logs", { query: { limit, action } }),
  },
};
