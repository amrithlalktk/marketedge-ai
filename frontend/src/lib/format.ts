// Formatting helpers. INR uses Indian digit grouping (₹5,00,000) via Intl en-IN.

const inr0 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 });
const inr2 = new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const int = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const DASH = "—";

function ok(v: number | null | undefined): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function inr(v: number | null | undefined, decimals: 0 | 2 = 2): string {
  if (!ok(v)) return DASH;
  return decimals === 0 ? inr0.format(v) : inr2.format(v);
}

export const CURRENCY_SYMBOL: Record<string, string> = {
  INR: "₹", USD: "$", EUR: "€", GBP: "£", JPY: "¥", HKD: "HK$", SGD: "S$", KRW: "₩", CAD: "C$", AUD: "A$", CHF: "CHF ", USDT: "$",
};

/** Decimal places appropriate for a price's magnitude: never force 2 dp on sub-cent tokens. */
export function pxDigits(v: number): number {
  const a = Math.abs(v);
  if (!Number.isFinite(a) || a === 0) return 2;
  if (a >= 1) return 2;
  if (a >= 0.01) return 4;
  return Math.min(10, Math.ceil(-Math.log10(a)) + 3);
}

/** Magnitude-aware number (no currency symbol). Pass `ref` to format a set of levels with one precision. */
export function px(v: number | null | undefined, opts: { ref?: number; currency?: string | null } = {}): string {
  if (!ok(v)) return DASH;
  const d = pxDigits(opts.ref ?? v);
  const loc = !opts.currency || opts.currency === "INR" ? "en-IN" : "en-US";
  return new Intl.NumberFormat(loc, { minimumFractionDigits: d, maximumFractionDigits: d }).format(v);
}

/** Price in instrument currency with its symbol ($ or ₹). INR uses Indian digit grouping. */
export function price(v: number | null | undefined, currency: string | null | undefined = "INR", opts: { ref?: number } = {}): string {
  if (!ok(v)) return DASH;
  const n = px(Math.abs(v), { ...opts, currency: currency ?? "INR", ref: opts.ref != null ? Math.abs(opts.ref) : undefined });
  const sym = CURRENCY_SYMBOL[currency ?? "INR"] ?? `${currency} `;
  return `${v < 0 ? "-" : ""}${sym}${n}`;
}

/** Money in a portfolio's base currency: ₹ with Indian grouping for INR, otherwise the currency symbol (e.g. $). */
export function money(v: number | null | undefined, currency: string | null | undefined = "INR", decimals: 0 | 2 = 2): string {
  if (!ok(v)) return DASH;
  if (!currency || currency === "INR") return inr(v, decimals);
  const n = new Intl.NumberFormat("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(Math.abs(v));
  return `${v < 0 ? "-" : ""}${CURRENCY_SYMBOL[currency] ?? `${currency} `}${n}`;
}

export function num(v: number | null | undefined, decimals = 2): string {
  if (!ok(v)) return DASH;
  return new Intl.NumberFormat("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(v);
}

export function integer(v: number | null | undefined): string {
  return ok(v) ? int.format(v) : DASH;
}

export function pct(v: number | null | undefined, decimals = 1, signed = false): string {
  if (!ok(v)) return DASH;
  const s = v.toFixed(decimals);
  return `${signed && v > 0 ? "+" : ""}${s}%`;
}

export function signedPct(v: number | null | undefined, decimals = 2): string {
  return pct(v, decimals, true);
}

/** Tailwind colour class for up/down movement. */
export function moveClass(v: number | null | undefined): string {
  if (!ok(v) || v === 0) return "text-muted";
  return v > 0 ? "text-up" : "text-down";
}

export function compact(v: number | null | undefined): string {
  if (!ok(v)) return DASH;
  const a = Math.abs(v);
  if (a >= 1e7) return `${(v / 1e7).toFixed(2)} Cr`;
  if (a >= 1e5) return `${(v / 1e5).toFixed(2)} L`;
  if (a >= 1e3) return `${(v / 1e3).toFixed(1)}K`;
  return int.format(v);
}

export function rr(v: number | null | undefined): string {
  return ok(v) ? `1:${v.toFixed(2)}` : DASH;
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso.length === 10 ? `${iso}T00:00:00` : iso);
  if (Number.isNaN(d.getTime())) return iso;
  return iso.length === 10
    ? d.toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" })
    : d.toLocaleString("en-IN", { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function period(p: [string, string] | null | undefined): string {
  if (!p || p.length < 2) return DASH;
  return `${p[0]} → ${p[1]}`;
}

export function humanize(key: string): string {
  return key.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Viewer-local date/time. */
export function localDateTime(iso: string | null | undefined): string {
  if (!iso) return DASH;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

/** "3h ago" / "in 5h" / "2d ago". */
export function relTime(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "";
  const ms = new Date(iso).getTime() - now;
  if (!Number.isFinite(ms)) return "";
  const a = Math.abs(ms);
  const m = Math.round(a / 60000);
  const txt = m < 1 ? "now" : m < 60 ? `${m}m` : m < 60 * 48 ? `${Math.round(m / 60)}h` : `${Math.round(m / 1440)}d`;
  if (txt === "now") return "just now";
  return ms < 0 ? `${txt} ago` : `in ${txt}`;
}
