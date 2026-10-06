"use client";

import Link from "next/link";
import { useState } from "react";
import { DirectionBadge, Disclaimer, EmptyState, ErrorState, PageHeader, Pill, Segmented, Skeleton, Stat, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { DASH, price, signedPct } from "@/lib/format";
import { marketOn } from "@/lib/market";
import type { IdeaResult, PastIdea } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type MarketFilter = "ALL" | "NFO" | "NSE" | "CRYPTO";
type ResultFilter = "all" | "closed" | "open";

const MARKET_LABEL: Record<PastIdea["market"], string> = { NFO: "NIFTY option", NSE: "NSE stock", CRYPTO: "Crypto" };

const RESULT: Record<IdeaResult, { label: string; tone: "green" | "red" | "amber" | "blue" | "slate" }> = {
  target2: { label: "Hit Target 2", tone: "green" },
  target1: { label: "Hit Target 1", tone: "green" },
  stop: { label: "Hit stop loss", tone: "red" },
  time: { label: "Time exit", tone: "amber" },
  open: { label: "Still open", tone: "blue" },
  not_filled: { label: "Not entered", tone: "slate" },
};

function resultDetail(i: PastIdea): string | null {
  if (i.result === "target1" && i.exit_reason?.startsWith("breakeven")) return "rest closed at entry";
  if (i.result === "target1" && i.exit_reason === "time") return "rest closed at the time limit";
  if (i.result === "time") return "neither target nor stop within the holding period";
  if (i.result === "not_filled") return "next open gapped past the entry or through the stop";
  if (i.result === "open") return "being tracked; updates after each daily scan";
  return null;
}

function Levels({ i }: { i: PastIdea }) {
  const p = (v: number | null | undefined) => (v == null ? DASH : price(v, i.currency));
  const [lo, hi] = i.entry_zone ?? [null, null];
  return (
    <p className="num mt-1 text-xs text-muted">
      Entry <span className="text-ink">{lo === hi ? p(lo) : `${p(lo)}–${p(hi)}`}</span> · Stop <span className="text-down">{p(i.stop)}</span> · Targets{" "}
      <span className="text-up">{p(i.targets?.[0])} / {p(i.targets?.[1])}</span>
      {i.judged_on && (
        <> · judged on NIFTY: stop {i.judged_on.stop}, targets {i.judged_on.targets.join(" / ")}</>
      )}
    </p>
  );
}

/** "833.12 → 850.00 ▲ +2.03% since the idea (in your favour)": what the price did after the idea was published. */
function Move({ i }: { i: PastIdea }) {
  const m = i.move;
  if (m.change_pct == null || m.price_then == null || m.price_now == null) return null;
  const up = m.change_pct > 0;
  const flat = m.change_pct === 0;
  const favour = flat ? null : (i.direction === "LONG") === up;
  const p = (v: number) => (i.market === "NFO" ? v.toLocaleString("en-IN", { maximumFractionDigits: 2 }) : price(v, i.currency));
  return (
    <p className="num mt-1 text-sm">
      <span className="text-muted">{i.market === "NFO" ? "NIFTY" : "Price"} </span>
      {p(m.price_then)} → {p(m.price_now)}{" "}
      <span className={cx("font-semibold", flat ? "text-muted" : up ? "text-up" : "text-down")}>
        {flat ? "unchanged" : `${up ? "▲ increased" : "▼ decreased"} ${signedPct(m.change_pct)}`}
      </span>
      <span className="text-xs text-muted">
        {" "}since the idea{m.as_of && ` (close of ${m.as_of})`}
        {favour != null && <> · <span className={favour ? "text-up" : "text-down"}>{favour ? "in your favour" : "against you"}</span></>}
      </span>
    </p>
  );
}

function IdeaRow({ i }: { i: PastIdea }) {
  const r = RESULT[i.result];
  const detail = resultDetail(i);
  const href = i.market === "NFO" ? "/options?tab=setups" : `/setups/${i.id}`;
  return (
    <li className="rounded-md border border-edge bg-panel2/40 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link href={href} className="font-semibold hover:underline">{i.label}</Link>
        <Pill tone="slate">{MARKET_LABEL[i.market]}</Pill>
        <DirectionBadge d={i.direction} />
        <span className="text-xs text-muted">{i.strategy}</span>
        <span className="ml-auto flex items-center gap-2">
          {i.net_return_pct != null && (
            <span className={cx("num text-sm font-semibold", i.net_return_pct >= 0 ? "text-up" : "text-down")}>
              {signedPct(i.net_return_pct)}
              {i.market === "NFO" && <span className="ml-1 text-[11px] font-normal text-muted">NIFTY move</span>}
            </span>
          )}
          <Pill tone={r.tone}>{r.label}</Pill>
        </span>
      </div>
      <Move i={i} />
      <Levels i={i} />
      <p className="mt-1 text-[11px] text-muted">
        Expected chance of Target 1: {i.chance_t1 != null && i.sample_size ? `${Math.round(i.chance_t1)}% (${i.sample_size} past cases)` : DASH}
        {detail && <> · {detail}</>}
      </p>
    </li>
  );
}

export default function TrackRecordPage() {
  const { can } = useAuth();
  const [market, setMarket] = useState<MarketFilter>("ALL");
  const [show, setShow] = useState<ResultFilter>("all");
  const q = useApi(() => api.signals.history(market === "ALL" ? undefined : market), [market]);

  const marketOptions = [
    { value: "ALL" as const, label: "All" },
    ...(marketOn("NSE") && can("options:signals") ? [{ value: "NFO" as const, label: "NIFTY options" }] : []),
    ...(marketOn("NSE") ? [{ value: "NSE" as const, label: "NSE stocks" }] : []),
    ...(marketOn("CRYPTO") ? [{ value: "CRYPTO" as const, label: "Crypto" }] : []),
  ];
  const items = (q.data?.items ?? []).filter((i) => show === "all" || (show === "open" ? i.result === "open" : i.result !== "open"));
  const byDate = items.reduce<Record<string, PastIdea[]>>((acc, i) => ((acc[i.as_of] ??= []).push(i), acc), {});
  const s = q.data?.summary;

  return (
    <>
      <PageHeader
        title="Track record"
        subtitle="Every idea the app published, followed day by day: did it reach its target or hit the stop loss first?"
      />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div>
          <span className="label">Market</span>
          <Segmented<MarketFilter> label="Market filter" value={market} onChange={setMarket} options={marketOptions} />
        </div>
        <div>
          <span className="label">Show</span>
          <Segmented<ResultFilter> label="Result filter" value={show} onChange={setShow}
            options={[{ value: "all", label: "All" }, { value: "closed", label: "Finished" }, { value: "open", label: "Still open" }]} />
        </div>
      </div>

      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="the track record" /> : !q.data || !s ? (
        <div className="space-y-2"><Skeleton className="h-20" /><Skeleton className="h-24" /><Skeleton className="h-24" /></div>
      ) : (
        <>
          <div className="mb-2 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Finished ideas" value={s.closed} sub={`${s.open} still open · ${s.not_filled} not entered`} />
            <Stat label="Reached Target 1" value={s.target1_rate != null ? `${s.target1_rate}%` : DASH} valueClass="text-up"
              sub={`${s.target1_or_better} of ${s.closed} · ${s.target2} went on to Target 2`} />
            <Stat label="Expected (history)" value={s.expected_target1_rate != null ? `${s.expected_target1_rate}%` : DASH}
              sub="average chance shown when published" />
            <Stat label="Hit stop loss first" value={s.stop_rate != null ? `${s.stop_rate}%` : DASH} valueClass="text-down" sub={`${s.stop} of ${s.closed}`} />
            <Stat label="Time exits" value={s.time} sub="no target or stop in time" />
            <Stat label="Avg result per idea" value={signedPct(s.avg_net_return_pct)}
              valueClass={s.avg_net_return_pct == null ? undefined : s.avg_net_return_pct >= 0 ? "text-up" : "text-down"} sub="after costs, per finished idea" />
          </div>
          <p className="mb-4 text-[11px] text-muted">
            {s.closed < 30 && s.closed > 0 && <strong className="text-amber-300">Only {s.closed} finished ideas so far: too few to judge the strategy; the rates will swing a lot. </strong>}
            {q.data.note}
          </p>

          {items.length === 0 ? (
            <EmptyState title={s.ideas === 0 ? "No ideas published yet" : "Nothing matches this filter"}>
              {s.ideas === 0 && "Ideas appear here after a daily scan publishes a setup that passes every safety check. Each one is then followed until it hits a target, the stop, or its time limit."}
            </EmptyState>
          ) : (
            <div className="space-y-4">
              {Object.entries(byDate).map(([d, list]) => (
                <section key={d} aria-label={`Ideas from ${d}`}>
                  <h2 className="mb-1.5 text-sm font-semibold">From the {d} close <span className="font-normal text-muted">· {list.length} idea{list.length > 1 ? "s" : ""}</span></h2>
                  <ul className="space-y-2">{list.map((i) => <IdeaRow key={i.id} i={i} />)}</ul>
                </section>
              ))}
            </div>
          )}
        </>
      )}
      <Disclaimer />
    </>
  );
}
