"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ImpactBadge } from "@/components/NewsEvents";
import { Card, Disclaimer, EmptyState, ErrorState, Field, PageHeader, Pill, Segmented, Skeleton, TableWrap, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { hoursUntil, istDateKey, istDateTime, moveClass, num, signedPct } from "@/lib/format";
import { isMarket, marketLabel } from "@/lib/market";
import type { EarningsEvent, EconomicEvent } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Tab = "economic" | "earnings";
type EMarket = "NSE" | "US" | "EUROPE" | "ASIA";
const COUNTRIES = ["IN", "US", "EU", "GB", "JP", "CN"];

function fmtVal(v: number | null | undefined, unit?: string | null) {
  if (v == null) return "—";
  return `${num(v, 2)}${unit && unit !== "%" ? ` ${unit}` : unit === "%" ? "%" : ""}`;
}

function Economic() {
  const [country, setCountry] = useState("");
  const [impact, setImpact] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const q = useApi(() => api.calendar.economic({ country: country || undefined, impact: impact || undefined, start: start || undefined, end: end || undefined }), [country, impact, start, end]);
  const now = Date.now();
  const groups = new Map<string, EconomicEvent[]>();
  for (const e of q.data?.items ?? []) {
    const k = istDateKey(e.event_time);
    groups.set(k, [...(groups.get(k) ?? []), e]);
  }
  const days = [...groups.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1));
  return (
    <>
      <form className="card mb-4" onSubmit={(e) => e.preventDefault()} aria-label="Economic calendar filters">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Field label="Country" htmlFor="c-cty">
            <select id="c-cty" className="input" value={country} onChange={(e) => setCountry(e.target.value)}>
              <option value="">All</option>{COUNTRIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Impact" htmlFor="c-imp">
            <select id="c-imp" className="input" value={impact} onChange={(e) => setImpact(e.target.value)}>
              <option value="">All</option>{["High", "Medium", "Low"].map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="From" htmlFor="c-s"><input id="c-s" type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
          <Field label="To" htmlFor="c-e"><input id="c-e" type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
        </div>
      </form>
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="economic calendar" /> : !q.data ? <Skeleton className="h-64" /> : (
        <>
          <p className="mb-2 text-xs text-muted">{q.data.start} → {q.data.end} · {q.data.items.length} releases · times in IST (hover for UTC) · <span className="rounded bg-amber-950/60 px-1 text-amber-200">highlighted</span> = within the next 24h</p>
          <p role="note" className="mb-3 rounded border border-edge bg-panel2/60 px-2 py-1.5 text-xs text-muted">ⓘ {q.data.impact_note}</p>
          {days.length === 0 ? <EmptyState title="No releases in this range" /> : (
            <div className="space-y-3">
              {days.map(([day, evs]) => (
                <Card key={day} title={new Date(`${day}T12:00:00Z`).toLocaleDateString("en-IN", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}>
                  <ul className="divide-y divide-edge">
                    {evs.sort((a, b) => (a.event_time < b.event_time ? -1 : 1)).map((e) => {
                      const h = hoursUntil(e.event_time, now);
                      const soon = h >= 0 && h <= 24;
                      const released = e.actual != null;
                      return (
                        <li key={e.id ?? `${e.name}-${e.event_time}`} className={cx("grid grid-cols-1 gap-1 py-2 text-sm sm:grid-cols-[6.5rem_1fr_auto] sm:items-center sm:gap-3", soon && "rounded bg-amber-950/30 px-1")}>
                          <time dateTime={e.event_time} title={`UTC ${e.event_time}`} className="num text-xs text-muted">{istDateTime(e.event_time, false)}{soon && <span className="ml-1 font-semibold text-amber-300">in {h.toFixed(1)}h</span>}</time>
                          <span className="min-w-0">
                            <span className="flex flex-wrap items-center gap-1.5">
                              <ImpactBadge impact={e.impact} />
                              <span className="rounded border border-edge px-1 font-mono text-[10px]" title={e.currency}>{e.country}</span>
                              <span className="break-words font-medium">{e.name}</span>
                              {e.is_sample && <Pill tone="amber">SAMPLE</Pill>}
                            </span>
                            <span className="block text-[11px] text-muted">{e.category}{e.currency ? ` · ${e.currency}` : ""}</span>
                          </span>
                          <dl className="grid grid-cols-4 gap-2 text-right text-xs sm:w-[20rem]">
                            <div><dt className="text-[10px] text-muted">Actual</dt><dd className="num font-semibold">{fmtVal(e.actual, e.unit)}</dd></div>
                            <div><dt className="text-[10px] text-muted">Forecast</dt><dd className="num">{fmtVal(e.forecast, e.unit)}</dd></div>
                            <div><dt className="text-[10px] text-muted">Previous</dt><dd className="num">{fmtVal(e.previous, e.unit)}</dd></div>
                            <div><dt className="text-[10px] text-muted">Surprise</dt><dd className={cx("num", released && moveClass(e.surprise))}>{released && e.surprise != null ? `${e.surprise > 0 ? "+" : ""}${num(e.surprise, 2)}` : "—"}</dd></div>
                          </dl>
                        </li>
                      );
                    })}
                  </ul>
                </Card>
              ))}
            </div>
          )}
        </>
      )}
    </>
  );
}

function EarningsTable({ rows, recent }: { rows: EarningsEvent[]; recent: boolean }) {
  if (rows.length === 0) return <EmptyState title={recent ? "No recent reports" : "No upcoming reports"} />;
  return (
    <TableWrap label={recent ? "Recent earnings" : "Upcoming earnings"}>
      <table className="tbl min-w-[640px] text-xs">
        <thead>
          <tr>
            <th scope="col">Symbol</th><th scope="col">Date</th><th scope="col">Time</th><th scope="col">Period</th>
            <th scope="col" className="text-right">EPS est.</th>{recent && <><th scope="col" className="text-right">EPS actual</th><th scope="col" className="text-right">EPS surprise</th></>}
            <th scope="col" className="text-right">Revenue est.</th>{recent && <><th scope="col" className="text-right">Revenue actual</th><th scope="col" className="text-right">Rev. surprise</th><th scope="col">Guidance</th></>}
            {!recent && <th scope="col">Event risk</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={`${r.symbol}-${r.event_date}`} className={cx(r.warning && "bg-amber-950/20")}>
              <td><Link className="link font-mono" href={`/stocks/${encodeURIComponent(r.symbol)}`}>{r.symbol}</Link></td>
              <td className="num whitespace-nowrap">{r.event_date}{r.days_until != null && <span className="block text-[10px] text-muted">{r.days_until >= 0 ? `in ${r.days_until}d` : `${-r.days_until}d ago`}</span>}</td>
              <td>{r.time === "bmo" ? "Before open" : r.time === "amc" ? "After close" : r.time ?? "—"}</td>
              <td>{r.period}</td>
              <td className="num text-right">{num(r.eps_estimate, 2)}</td>
              {recent && <><td className="num text-right">{num(r.eps_actual, 2)}</td><td className={cx("num text-right font-semibold", moveClass(r.eps_surprise_pct))}>{signedPct(r.eps_surprise_pct)}</td></>}
              <td className="num text-right">{num(r.revenue_estimate, 1)}</td>
              {recent && <><td className="num text-right">{num(r.revenue_actual, 1)}</td><td className={cx("num text-right font-semibold", moveClass(r.revenue_surprise_pct))}>{signedPct(r.revenue_surprise_pct)}</td>
                <td>{r.guidance ? <Pill tone={r.guidance === "raised" ? "green" : r.guidance === "lowered" ? "red" : "slate"}>{r.guidance}</Pill> : "—"}</td></>}
              {!recent && <td className={r.warning ? "font-semibold text-amber-300" : "text-muted"}>{r.warning ?? "—"}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </TableWrap>
  );
}

function Earnings({ initial }: { initial: EMarket }) {
  const [market, setMarket] = useState<EMarket>(initial);
  const q = useApi(() => api.calendar.earnings({ market }), [market]);
  return (
    <>
      <div className="mb-3"><Segmented<EMarket> label="Earnings market" value={market} onChange={setMarket} options={(["NSE", "US", "EUROPE", "ASIA"] as const).map((m) => ({ value: m, label: marketLabel(m) }))} /></div>
      <p role="note" className="mb-3 rounded border border-edge bg-panel2/60 px-2 py-1.5 text-xs text-muted">ⓘ Earnings within 3 days block new setups for that stock; within 10 days they add a warning. Crypto and forex have no earnings.</p>
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="earnings calendar" /> : !q.data ? <Skeleton className="h-64" /> : (
        <div className="space-y-4">
          <Card title={`Upcoming (${q.data.upcoming.length}) · to ${q.data.end}`}>
            {q.data.upcoming.filter((u) => u.warning).length > 0 && <p className="mb-2 text-xs text-amber-300">⚠ {q.data.upcoming.filter((u) => u.warning).length} report(s) within 10 days — high event risk for those symbols.</p>}
            <EarningsTable rows={q.data.upcoming} recent={false} />
          </Card>
          <Card title={`Recent (${q.data.recent.length}) · from ${q.data.start}`}><EarningsTable rows={q.data.recent} recent /></Card>
        </div>
      )}
    </>
  );
}

function CalendarInner() {
  const params = useSearchParams();
  const router = useRouter();
  const tab: Tab = params.get("tab") === "earnings" ? "earnings" : "economic";
  const m = params.get("market");
  const initial: EMarket = isMarket(m) && ["NSE", "US", "EUROPE", "ASIA"].includes(m) ? (m as EMarket) : "NSE";
  return (
    <>
      <PageHeader title="Calendars" subtitle="Economic releases and company earnings that feed the platform's event-risk checks." />
      <div className="mb-4"><Segmented<Tab> label="Calendar" value={tab} onChange={(t) => router.replace(t === "economic" ? "/calendar" : "/calendar?tab=earnings", { scroll: false })} options={[{ value: "economic", label: "Economic" }, { value: "earnings", label: "Earnings" }]} /></div>
      {tab === "economic" ? <Economic /> : <Earnings initial={initial} />}
      <Disclaimer />
    </>
  );
}

export default function CalendarPage() {
  return (
    <Suspense>
      <CalendarInner />
    </Suspense>
  );
}
