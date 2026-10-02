"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { SetupCard } from "@/components/SetupCard";
import { DataStamp, Disclaimer, EmptyState, ErrorState, PageHeader, Segmented, Skeleton, UpgradeNote, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { SORT_OPTIONS } from "@/lib/constants";
import { featureOn, marketLabel, marketOn, useMarket, withMarket, type MarketId } from "@/lib/market";
import type { OptionSignals, SortKey, TopSetups } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Tab = "valid" | "no_trade";
type Dir = "ALL" | "LONG" | "SHORT";
type Region = "ALL" | "US" | "EUROPE" | "ASIA";

function Grid({ children }: { children: React.ReactNode }) {
  return <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3">{children}</div>;
}

function Controls({ id, sort, setSort, dir, setDir, extra }: { id: string; sort: SortKey; setSort: (s: SortKey) => void; dir: Dir; setDir: (d: Dir) => void; extra?: React.ReactNode }) {
  return (
    <div className="mb-3 flex flex-wrap items-end gap-3">
      <div>
        <label className="label" htmlFor={`sort-${id}`}>Sort by</label>
        <select id={`sort-${id}`} className="input w-auto" value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
          {SORT_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </div>
      <div>
        <span className="label">Direction</span>
        <Segmented<Dir> label={`Direction filter ${id}`} value={dir} onChange={setDir} options={[{ value: "ALL", label: "All" }, { value: "LONG", label: "Long" }, { value: "SHORT", label: "Short" }]} />
      </div>
      {extra}
    </div>
  );
}

function NoValid({ message }: { message?: string | null }) {
  return (
    <div className="rounded-lg border border-amber-800 bg-amber-950/30 p-4 text-center">
      <p className="text-sm font-bold text-amber-200">NO VALID SETUP</p>
      <p className="mt-1 text-xs text-muted">{message ?? "No candidate passed every validation check in the latest scan."}</p>
    </div>
  );
}

function Section({ id, title, subtitle, children, right }: { id: string; title: string; subtitle?: React.ReactNode; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section id={`sec-${id}`} aria-labelledby={`h-${id}`} className="scroll-mt-4 rounded-lg border border-edge bg-panel/40 p-3 sm:p-4">
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <h2 id={`h-${id}`} className="text-base font-semibold">{title}</h2>
          {subtitle && <p className="text-xs text-muted">{subtitle}</p>}
        </div>
        {right}
      </div>
      {children}
    </section>
  );
}

function MarketSection({ market, title }: { market: Exclude<MarketId, "US" | "EUROPE" | "ASIA">; title: string }) {
  const { can } = useAuth();
  const [sort, setSort] = useState<SortKey>("score");
  const [dir, setDir] = useState<Dir>("ALL");
  const q = useApi<TopSetups>(() => api.signals.top({ market, sort, direction: dir === "ALL" ? undefined : dir, limit: 30 }), [market, sort, dir]);
  const d = q.data;
  return (
    <Section id={market} title={title} subtitle={d ? <>scan #{d.scan_run_id} · <DataStamp asOf={d.as_of} /></> : undefined}
      right={<Link className="link text-xs" href={withMarket("/", market)}>Market overview →</Link>}>
      <Controls id={market} sort={sort} setSort={setSort} dir={dir} setDir={setDir} />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what={`${title} setups`} /> : !d ? <Skeleton className="h-40" /> : (
        <>
          <p className="mb-2 text-xs text-muted">
            <span className="num text-ink">{d.items.length}</span> shown of <span className="num text-ink">{d.total_valid}</span> valid
            {!can("signals:read_all") && d.total_valid > d.items.length && " · Standard plan shows the top 5"} · {d.sort_note}
          </p>
          {d.items.length === 0 ? <NoValid message={d.market_message} /> : (
            <>
              {d.market_message && <p className="mb-2 rounded border border-amber-800 bg-amber-950/30 px-3 py-2 text-sm font-semibold text-amber-200">{d.market_message}</p>}
              <Grid>{d.items.map((s) => <SetupCard key={s.id} s={s} />)}</Grid>
            </>
          )}
        </>
      )}
    </Section>
  );
}

function GlobalSection() {
  const { can } = useAuth();
  const [sort, setSort] = useState<SortKey>("score");
  const [dir, setDir] = useState<Dir>("ALL");
  const [region, setRegion] = useState<Region>("ALL");
  const q = useApi<TopSetups>(() => api.signals.global({ region: region === "ALL" ? undefined : region, sort, direction: dir === "ALL" ? undefined : dir, limit: 30 }), [region, sort, dir]);
  const d = q.data;
  return (
    <Section id="GLOBAL" title="US / Global stocks" subtitle="US (NYSE/NASDAQ), Europe (LSE/XETRA/Euronext), Asia (TSE/HKEX/SGX/KRX)">
      <Controls id="global" sort={sort} setSort={setSort} dir={dir} setDir={setDir} extra={
        <div>
          <span className="label">Region</span>
          <Segmented<Region> label="Region filter" value={region} onChange={setRegion} options={[{ value: "ALL", label: "All" }, { value: "US", label: "US" }, { value: "EUROPE", label: "Europe" }, { value: "ASIA", label: "Asia" }]} />
        </div>
      } />
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="global setups" /> : !d ? <Skeleton className="h-40" /> : (
        <>
          {d.markets && (
            <ul className="mb-2 flex flex-wrap gap-2 text-[11px] text-muted">
              {Object.entries(d.markets).map(([m, r]) => (
                <li key={m} className="rounded border border-edge px-2 py-1">
                  <span className="font-semibold text-ink">{marketLabel(m)}</span> · scan #{r.scan_run_id} · {r.as_of}
                  {r.market_message && <span className="block text-amber-300">{r.market_message}</span>}
                </li>
              ))}
            </ul>
          )}
          <p className="mb-2 text-xs text-muted"><span className="num text-ink">{d.items.length}</span> shown of <span className="num text-ink">{d.total_valid}</span> valid{!can("signals:read_all") && d.total_valid > d.items.length && " · Standard plan shows the top 5"}</p>
          {d.items.length === 0 ? <NoValid message={d.market_message} /> : <Grid>{d.items.map((s) => <SetupCard key={s.id} s={s} />)}</Grid>}
        </>
      )}
    </Section>
  );
}

function OptionsSection() {
  const { can } = useAuth();
  const ok = can("options:signals");
  const q = useApi<OptionSignals>(() => api.options.signals(), [], ok);
  const valid = q.data?.items.filter((i) => i.status === "VALID") ?? [];
  return (
    <Section id="NFO" title="NIFTY Options" subtitle="Option contracts selected from underlying NIFTY setups" right={<Link className="link text-xs" href="/options?tab=setups">Open options view →</Link>}>
      {!ok || q.error?.status === 403 ? <UpgradeNote feature="Option setups" /> : q.error ? <ErrorState error={q.error} onRetry={q.reload} what="option setups" /> : !q.data ? <Skeleton className="h-20" /> : (
        valid.length === 0 ? <NoValid message={q.data.market_message} /> : (
          <ul className="grid grid-cols-1 gap-2 md:grid-cols-2">
            {valid.map((s, i) => (
              <li key={i} className="card text-sm">
                <p className="font-mono font-semibold">{s.contract?.label ?? s.direction}</p>
                <p className="text-xs text-muted">{s.direction} · entry ₹{s.entry} · T2 ₹{s.targets?.[1]} · stop ₹{s.stop} · score {s.score} · Hist. T1 {s.probability?.sample_size ? `${s.probability.t1_hit_rate}% (n=${s.probability.sample_size})` : "insufficient history"}</p>
                <Link href="/options?tab=setups" className="link text-xs">Full option setup →</Link>
              </li>
            ))}
          </ul>
        )
      )}
      {q.data && <p className="mt-2 text-[11px] text-muted">{q.data.items.length} candidate(s) evaluated · market state {q.data.market_state.direction}</p>}
    </Section>
  );
}

function NoTradeTab() {
  const { can } = useAuth();
  const [market] = useMarket();
  const [sort, setSort] = useState<SortKey>("score");
  const [dir, setDir] = useState<Dir>("ALL");
  const allowed = can("signals:read_all");
  const list = useApi(() => api.signals.list({ status: "NO_TRADE", sort, direction: dir === "ALL" ? undefined : dir, market }), [sort, dir, market], allowed);
  if (!allowed || list.error?.status === 403) return <UpgradeNote feature="The NO TRADE candidate list" />;
  return (
    <>
      <div className="mb-3"><MarketSwitcher /></div>
      <Controls id="nt" sort={sort} setSort={setSort} dir={dir} setDir={setDir} />
      {list.error ? <ErrorState error={list.error} onRetry={list.reload} what="NO TRADE candidates" /> : !list.data ? <Grid>{[0, 1, 2].map((i) => <Skeleton key={i} className="h-72" />)}</Grid> : (
        <>
          <p className="mb-3 text-xs text-muted">
            {marketLabel(market)}: candidates whose strategy fired but which failed at least one blocking validation check. They are <strong className="text-ink">not</strong> trade setups. <DataStamp asOf={list.data.as_of} />
          </p>
          {list.data.items.length === 0 ? <EmptyState title={`No rejected candidates in the latest ${marketLabel(market)} scan`} /> : <Grid>{list.data.items.map((s) => <SetupCard key={s.id} s={s} showBlocking />)}</Grid>}
        </>
      )}
    </>
  );
}

const ORDER: { id: string; node: () => React.ReactNode; label: string }[] = [
  { id: "NFO", label: "NIFTY Options", node: () => <OptionsSection /> },
  { id: "NSE", label: "Indian Stocks", node: () => <MarketSection market="NSE" title="Indian Stocks (NSE)" /> },
  { id: "CRYPTO", label: "Crypto", node: () => <MarketSection market="CRYPTO" title="Crypto" /> },
  { id: "GLOBAL", label: "US / Global", node: () => <GlobalSection /> },
  { id: "FX", label: "Forex", node: () => <MarketSection market="FX" title="Forex" /> },
];

function SetupsInner() {
  const params = useSearchParams();
  const router = useRouter();
  const [market] = useMarket();
  const tab: Tab = params.get("tab") === "no_trade" ? "no_trade" : "valid";
  const setTab = (t: Tab) => {
    const p = new URLSearchParams(params.toString());
    if (t === "valid") p.delete("tab");
    else p.set("tab", "no_trade");
    const q = p.toString();
    router.replace(q ? `/setups?${q}` : "/setups", { scroll: false });
  };
  const focus = ["US", "EUROPE", "ASIA"].includes(market) ? "GLOBAL" : market;
  const on = (id: string) =>
    id === "NFO" ? marketOn("NSE") && featureOn("options") : id === "GLOBAL" ? (["US", "EUROPE", "ASIA"] as const).some(marketOn) : marketOn(id as MarketId);
  const shown = ORDER.filter((o) => on(o.id));
  const sections = market === "NSE" ? shown : [...shown.filter((o) => o.id === focus), ...shown.filter((o) => o.id !== focus)];

  return (
    <>
      <PageHeader title="Today's top setups" subtitle="Setups that passed every validation check in each market's latest scan. Each market is scanned and validated separately." />
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <Segmented<Tab> label="Setup list" value={tab} onChange={setTab} options={[{ value: "valid", label: "Valid setups" }, { value: "no_trade", label: "NO TRADE candidates" }]} />
      </div>
      {tab === "valid" ? (
        <>
          <nav aria-label="Jump to market" className="mb-4 flex flex-wrap gap-1 text-xs">
            {sections.map((s) => <a key={s.id} href={`#sec-${s.id}`} className={cx("rounded border border-edge px-2 py-1 hover:bg-panel2", s.id === focus && "border-accent text-ink")}>{s.label}</a>)}
          </nav>
          <p className="-mt-2 mb-4 text-[11px] text-muted">Prices are in each instrument&apos;s own currency; INR equivalents are shown where available.</p>
          <div className="space-y-4">{sections.map((s) => <div key={s.id}>{s.node()}</div>)}</div>
        </>
      ) : <NoTradeTab />}
      <Disclaimer />
    </>
  );
}

export default function SetupsPage() {
  return (
    <Suspense>
      <SetupsInner />
    </Suspense>
  );
}
