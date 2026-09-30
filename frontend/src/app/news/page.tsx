"use client";

import { Suspense, useEffect, useState } from "react";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { NEWS_NOTE, NewsCard } from "@/components/NewsEvents";
import { Disclaimer, EmptyState, ErrorState, Field, PageHeader, Segmented, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { marketLabel, useMarket } from "@/lib/market";
import type { NewsItem } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const CATEGORIES = ["earnings", "regulatory", "insider", "analyst", "macro", "geopolitical", "announcement", "general"];
type Sent = "ALL" | "Positive" | "Neutral" | "Negative";

function NewsInner() {
  const [market] = useMarket();
  const [allMarkets, setAllMarkets] = useState(false);
  const [symbol, setSymbol] = useState("");
  const [sym, setSym] = useState("");
  const [sentiment, setSentiment] = useState<Sent>("ALL");
  const [category, setCategory] = useState("");
  const [days, setDays] = useState(14);
  useEffect(() => {
    const t = setTimeout(() => setSym(symbol.trim().toUpperCase()), 350);
    return () => clearTimeout(t);
  }, [symbol]);
  const q = useApi<{ items: NewsItem[]; note: string }>(
    () => api.news.list({ market: allMarkets || sym ? undefined : market, symbol: sym || undefined, sentiment: sentiment === "ALL" ? undefined : sentiment, category: category || undefined, days, limit: 100 }),
    [market, allMarkets, sym, sentiment, category, days],
  );
  const items = q.data?.items ?? [];
  const counts = items.reduce<Record<string, number>>((a, n) => ({ ...a, [n.sentiment_label]: (a[n.sentiment_label] ?? 0) + 1 }), {});

  return (
    <>
      <PageHeader title={`News & sentiment — ${sym ? sym : allMarkets ? "all markets" : marketLabel(market)}`} subtitle="Headlines tagged to instruments and markets, with a transparent lexicon-based sentiment label." right={<MarketSwitcher />} />
      <p role="note" className="mb-3 rounded border border-blue-900 bg-blue-950/30 px-3 py-2 text-xs text-blue-100">ⓘ {q.data?.note ?? NEWS_NOTE}</p>
      <form className="card mb-4" onSubmit={(e) => e.preventDefault()} aria-label="News filters">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
          <Field label="Symbol" htmlFor="n-sym"><input id="n-sym" className="input font-mono" placeholder="e.g. DEMO_030" maxLength={64} value={symbol} onChange={(e) => setSymbol(e.target.value)} /></Field>
          <Field label="Category" htmlFor="n-cat">
            <select id="n-cat" className="input" value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="">All</option>{CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
            </select>
          </Field>
          <Field label="Period" htmlFor="n-days">
            <select id="n-days" className="input" value={days} onChange={(e) => setDays(Number(e.target.value))}>
              {[1, 3, 7, 14, 30, 90].map((d) => <option key={d} value={d}>Last {d} day{d > 1 ? "s" : ""}</option>)}
            </select>
          </Field>
          <div className="col-span-2 sm:col-span-1 lg:col-span-2">
            <span className="label">Sentiment</span>
            <Segmented<Sent> label="Sentiment filter" value={sentiment} onChange={setSentiment} options={[{ value: "ALL", label: "All" }, { value: "Positive", label: "Positive" }, { value: "Neutral", label: "Neutral" }, { value: "Negative", label: "Negative" }]} />
          </div>
          <label className="col-span-2 inline-flex items-center gap-2 text-sm sm:col-span-4 lg:col-span-5"><input type="checkbox" checked={allMarkets} onChange={(e) => setAllMarkets(e.target.checked)} /> All markets{sym ? " (a symbol search always spans markets)" : ""}</label>
        </div>
      </form>
      {q.error ? <ErrorState error={q.error} onRetry={q.reload} what="news" /> : !q.data ? <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-24" />)}</div> : items.length === 0 ? (
        <EmptyState title="No articles match these filters">Try a longer period or another market.</EmptyState>
      ) : (
        <>
          <p className="mb-2 text-xs text-muted">
            {items.length} article{items.length === 1 ? "" : "s"} · <span className="text-up">{counts.Positive ?? 0} positive</span> · {counts.Neutral ?? 0} neutral · <span className="text-down">{counts.Negative ?? 0} negative</span> · newest first · times in your local time zone
          </p>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">{items.map((n) => <NewsCard key={n.id} n={n} />)}</div>
        </>
      )}
      <Disclaimer />
    </>
  );
}

export default function NewsPage() {
  return (
    <Suspense>
      <NewsInner />
    </Suspense>
  );
}
