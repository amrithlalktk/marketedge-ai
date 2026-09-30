"use client";

import Link from "next/link";
import { useId, useState } from "react";
import { ApiError } from "@/lib/api";
import { DISCLAIMER } from "@/lib/constants";
import { dateTime } from "@/lib/format";
import type { DataMeta, Direction } from "@/lib/types";

export function cx(...c: (string | false | null | undefined)[]) {
  return c.filter(Boolean).join(" ");
}

export function Card({ title, right, children, className, id }: { title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string; id?: string }) {
  return (
    <section className={cx("card min-w-0", className)} id={id} aria-labelledby={title && id ? `${id}-h` : undefined}>
      {(title || right) && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          {title && (
            <h2 className="card-title" id={id ? `${id}-h` : undefined}>
              {title}
            </h2>
          )}
          {right}
        </div>
      )}
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <header className="mb-4 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        <h1 className="text-lg font-semibold tracking-tight sm:text-xl">{title}</h1>
        {subtitle && <p className="mt-0.5 text-sm text-muted">{subtitle}</p>}
      </div>
      {right}
    </header>
  );
}

export function DirectionBadge({ d }: { d: Direction | string }) {
  return <span className={cx("badge", d === "LONG" ? "bg-green-950 text-green-300 ring-1 ring-green-800" : "bg-red-950 text-red-300 ring-1 ring-red-800")}>{d}</span>;
}

export function StatusBadge({ s }: { s: string }) {
  const cls =
    s === "VALID" ? "bg-green-950 text-green-300 ring-green-800" : s === "NO_TRADE" ? "bg-amber-950 text-amber-300 ring-amber-800" : "bg-slate-800 text-slate-300 ring-slate-700";
  return <span className={cx("badge ring-1", cls)}>{s.replace("_", " ")}</span>;
}

export function Pill({ children, tone = "slate" }: { children: React.ReactNode; tone?: "slate" | "green" | "red" | "amber" | "blue" }) {
  const map = {
    slate: "bg-slate-800 text-slate-200 ring-slate-700",
    green: "bg-green-950 text-green-300 ring-green-800",
    red: "bg-red-950 text-red-300 ring-red-800",
    amber: "bg-amber-950 text-amber-300 ring-amber-800",
    blue: "bg-blue-950 text-blue-300 ring-blue-800",
  } as const;
  return <span className={cx("badge ring-1", map[tone])}>{children}</span>;
}

/** Data timestamp + sample/delayed badges. Shown on every setup and market card. */
export function DataStamp({ meta, asOf, className }: { meta?: DataMeta | null; asOf?: string | null; className?: string }) {
  const ts = asOf ?? meta?.last_bar ?? null;
  return (
    <span className={cx("inline-flex flex-wrap items-center gap-1.5 text-[11px] text-muted", className)}>
      <span>
        Data as of <time className="num">{ts ? dateTime(ts.slice(0, 10)) : "—"}</time>
      </span>
      {meta?.delayed && <span className="badge bg-amber-950 text-amber-300 ring-1 ring-amber-700">⚠ DATA DELAYED</span>}
      {meta?.is_sample && <span className="badge bg-fuchsia-950 text-fuchsia-300 ring-1 ring-fuchsia-800">SAMPLE</span>}
    </span>
  );
}

export function Loading({ label = "Loading…" }: { label?: string }) {
  return (
    <div role="status" aria-live="polite" className="flex items-center gap-2 py-6 text-sm text-muted">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-edge border-t-accent" aria-hidden />
      {label}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cx("animate-pulse rounded bg-panel2", className)} aria-hidden />;
}

export function EmptyState({ title, children }: { title: string; children?: React.ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-edge p-4 text-center text-sm text-muted">
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="mt-1">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry, what }: { error: ApiError | Error | null; onRetry?: () => void; what?: string }) {
  if (!error) return null;
  const status = error instanceof ApiError ? error.status : 0;
  const msg = error.message;
  let title = what ? `Could not load ${what}` : "Something went wrong";
  let hint: React.ReactNode = null;
  if (status === 404 && /scan|computed|ingest/i.test(msg)) {
    title = "No scan has completed yet";
    hint = (
      <>
        Market data and setups appear after an administrator runs <strong>data ingestion</strong> and then a <strong>scan</strong> (Admin → Jobs).
      </>
    );
  } else if (status === 403) {
    title = "Permission required";
    hint = <>Your account role does not include this feature. Ask an administrator to upgrade your plan (Premium unlocks the full setup list, NO TRADE candidates and backtests).</>;
  } else if (status === 429) {
    title = "Rate limit reached";
    hint = <>Too many requests in the last minute. Wait a moment and retry.</>;
  } else if (status === 0) {
    hint = <>The API may be unreachable. Check that the backend is running.</>;
  }
  return (
    <div role="alert" className="rounded-md border border-red-900/70 bg-red-950/40 p-3 text-sm">
      <p className="font-semibold text-red-200">{title}</p>
      <p className="mt-1 text-red-200/80">{msg}</p>
      {hint && <p className="mt-1 text-muted">{hint}</p>}
      {onRetry && status !== 403 && (
        <button className="btn-ghost mt-2" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}

export function InlineError({ error }: { error: string | null }) {
  if (!error) return null;
  return (
    <p role="alert" className="mt-2 rounded border border-red-900/70 bg-red-950/40 px-2 py-1.5 text-sm text-red-200">
      {error}
    </p>
  );
}

export function Disclaimer() {
  return (
    <aside aria-label="Disclaimer" className="mt-6 rounded-md border border-edge bg-panel/60 p-3 text-xs leading-relaxed text-muted">
      <strong className="text-ink">Disclaimer: </strong>
      {DISCLAIMER}
    </aside>
  );
}

export function Stat({ label, value, sub, className, valueClass }: { label: React.ReactNode; value: React.ReactNode; sub?: React.ReactNode; className?: string; valueClass?: string }) {
  return (
    <div className={cx("min-w-0 rounded-md border border-edge/70 bg-panel2/60 px-2.5 py-2", className)}>
      <div className="truncate text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={cx("num mt-0.5 break-words text-sm font-semibold sm:text-base", valueClass)}>{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-muted">{sub}</div>}
    </div>
  );
}

export function Bar({ value, max = 100, tone }: { value: number | null | undefined; max?: number; tone?: "green" | "red" | "blue" | "amber" }) {
  const v = typeof value === "number" && Number.isFinite(value) ? Math.max(0, Math.min(max, value)) : 0;
  const t = tone ?? (v >= 75 ? "green" : v >= 50 ? "blue" : v >= 30 ? "amber" : "red");
  const color = { green: "bg-up", red: "bg-down", blue: "bg-accent", amber: "bg-warn" }[t];
  return (
    <div className="h-2 w-full overflow-hidden rounded bg-edge" role="presentation">
      <div className={cx("h-full rounded", color)} style={{ width: `${(100 * v) / max}%` }} />
    </div>
  );
}

export function Collapsible({ title, children, defaultOpen = false, right }: { title: React.ReactNode; children: React.ReactNode; defaultOpen?: boolean; right?: React.ReactNode }) {
  return (
    <details className="group rounded-lg border border-edge bg-panel" open={defaultOpen}>
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 rounded-lg px-3 py-2.5 text-sm font-semibold hover:bg-panel2 focus-visible:ring-2 focus-visible:ring-accent sm:px-4">
        <span className="flex min-w-0 items-center gap-2">
          <span className="text-muted transition-transform group-open:rotate-90" aria-hidden>
            ▸
          </span>
          {title}
        </span>
        {right}
      </summary>
      <div className="border-t border-edge p-3 sm:p-4">{children}</div>
    </details>
  );
}

export function Segmented<T extends string>({ value, onChange, options, label }: { value: T; onChange: (v: T) => void; options: readonly { value: T; label: string }[]; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex max-w-full flex-wrap rounded-md border border-edge bg-panel2 p-0.5">
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          role="radio"
          aria-checked={value === o.value}
          onClick={() => onChange(o.value)}
          className={cx(
            "rounded px-2.5 py-1 text-xs font-medium focus:outline-none focus-visible:ring-2 focus-visible:ring-accent",
            value === o.value ? "bg-accent text-white" : "text-muted hover:text-ink",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function Field({ label, children, hint, htmlFor }: { label: string; children: React.ReactNode; hint?: React.ReactNode; htmlFor?: string }) {
  return (
    <div className="min-w-0">
      <label className="label" htmlFor={htmlFor}>
        {label}
      </label>
      {children}
      {hint && <p className="mt-1 text-[11px] text-muted">{hint}</p>}
    </div>
  );
}

export function NumberInput({ label, value, onChange, step = "any", min, max, hint, suffix }: { label: string; value: string; onChange: (v: string) => void; step?: string; min?: number; max?: number; hint?: React.ReactNode; suffix?: string }) {
  const id = useId();
  return (
    <Field label={suffix ? `${label} (${suffix})` : label} htmlFor={id} hint={hint}>
      <input id={id} className="input num" inputMode="decimal" type="number" step={step} min={min} max={max} value={value} onChange={(e) => onChange(e.target.value)} />
    </Field>
  );
}

export function Check({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <li className="flex gap-2">
      <span aria-hidden className={ok ? "text-up" : "text-down"}>
        {ok ? "✓" : "✗"}
      </span>
      <span className="sr-only">{ok ? "Agrees:" : "Disagrees:"}</span>
      <span className="min-w-0">{children}</span>
    </li>
  );
}

export function TableWrap({ children, label }: { children: React.ReactNode; label?: string }) {
  return (
    <div className="-mx-3 overflow-x-auto px-3 sm:mx-0 sm:px-0" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

export function UpgradeNote({ feature }: { feature: string }) {
  return (
    <div className="rounded-md border border-blue-900 bg-blue-950/40 p-3 text-sm">
      <p className="font-semibold text-blue-200">{feature} requires a Premium plan</p>
      <p className="mt-1 text-muted">Your current role cannot access this. An administrator can change your role under Admin → Users.</p>
    </div>
  );
}

export function Confirm({ label, onConfirm, className, children }: { label: string; onConfirm: () => void; className?: string; children: React.ReactNode }) {
  const [asking, setAsking] = useState(false);
  if (!asking)
    return (
      <button type="button" className={className ?? "btn-ghost"} onClick={() => setAsking(true)}>
        {children}
      </button>
    );
  return (
    <span className="inline-flex items-center gap-1">
      <button type="button" className="btn-danger px-2 py-1 text-xs" onClick={() => { setAsking(false); onConfirm(); }}>
        {label}
      </button>
      <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setAsking(false)}>
        Cancel
      </button>
    </span>
  );
}

export function SymbolLink({ symbol }: { symbol: string }) {
  return (
    <Link className="link font-mono font-semibold" href={`/stocks/${encodeURIComponent(symbol)}`}>
      {symbol}
    </Link>
  );
}
