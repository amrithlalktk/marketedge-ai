"use client";

import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { marketLabel, useMarket } from "@/lib/market";
import { Disclaimer, EmptyState, ErrorState, PageHeader, Pill, Skeleton, TableWrap } from "@/components/ui";
import { api } from "@/lib/api";
import type { StockList } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const PAGE_SIZE = 50;

function StocksInner() {
  const [market] = useMarket();
  const [all, setAll] = useState(false);
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState(1);
  const [indices, setIndices] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => {
      setDebounced(q.trim());
      setPage(1);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);
  useEffect(() => setPage(1), [market, all]);
  const list = useApi<StockList>(() => api.stocks.list({ q: debounced || undefined, page, page_size: PAGE_SIZE, include_indices: indices, market: all ? undefined : market }), [debounced, page, indices, market, all]);
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <PageHeader title={`Instruments — ${all ? "all markets" : marketLabel(market)}`} subtitle="Search stocks, coins and currency pairs; open charts and analysis." right={<MarketSwitcher />} />
      <form role="search" className="mb-3 flex flex-wrap items-end gap-3" onSubmit={(e) => e.preventDefault()}>
        <div className="min-w-0 flex-1">
          <label className="label" htmlFor="q">Symbol or name</label>
          <input id="q" className="input" type="search" placeholder="e.g. RELIANCE, bank…" maxLength={64} value={q} onChange={(e) => setQ(e.target.value)} />
        </div>
        <label className="inline-flex items-center gap-2 pb-2 text-sm">
          <input type="checkbox" checked={indices} onChange={(e) => { setIndices(e.target.checked); setPage(1); }} /> Include indices
        </label>
        <label className="inline-flex items-center gap-2 pb-2 text-sm">
          <input type="checkbox" checked={all} onChange={(e) => setAll(e.target.checked)} /> Search all markets
        </label>
      </form>
      {list.error && <ErrorState error={list.error} onRetry={list.reload} what="instruments" />}
      {list.loading && !list.data && <Skeleton className="h-64" />}
      {list.data && (
        list.data.items.length === 0 ? (
          <EmptyState title="No instruments match your search" />
        ) : (
          <>
            <p className="mb-2 text-xs text-muted"><span className="num text-ink">{list.data.total}</span> instruments</p>
            {/* Mobile: cards */}
            <ul className="space-y-2 md:hidden">
              {list.data.items.map((i) => (
                <li key={i.symbol}>
                  <Link href={`/stocks/${encodeURIComponent(i.symbol)}`} className="card flex items-center justify-between gap-2 hover:bg-panel2">
                    <span className="min-w-0">
                      <span className="block font-mono font-semibold">{i.symbol}</span>
                      <span className="block truncate text-xs text-muted">{i.name}</span>
                    </span>
                    <span className="flex shrink-0 flex-col items-end gap-1 text-xs text-muted">
                      {marketLabel(i.market)} · {i.currency}
                      <span>{i.sector ?? (i.is_index ? "Index" : "—")}</span>
                      {i.is_sample && <Pill tone="amber">SAMPLE</Pill>}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            {/* Desktop: dense table */}
            <div className="card hidden md:block">
              <TableWrap label="Instruments">
                <table className="tbl">
                  <thead>
                    <tr><th scope="col">Symbol</th><th scope="col">Name</th><th scope="col">Market</th><th scope="col">Sector / group</th><th scope="col">Exchange</th><th scope="col">Ccy</th><th scope="col">Class</th><th scope="col">Data</th></tr>
                  </thead>
                  <tbody>
                    {list.data.items.map((i) => (
                      <tr key={i.symbol} className="hover:bg-panel2">
                        <td><Link className="link font-mono font-semibold" href={`/stocks/${encodeURIComponent(i.symbol)}`}>{i.symbol}</Link></td>
                        <td className="max-w-[22rem] truncate">{i.name}</td>
                        <td className="text-xs">{marketLabel(i.market)}</td>
                        <td>{i.sector ?? "—"}</td>
                        <td>{i.exchange}</td>
                        <td className="font-mono text-xs">{i.currency}</td>
                        <td className="text-xs text-muted">{i.is_index ? "INDEX" : i.asset_class}</td>
                        <td>{i.is_sample ? <Pill tone="amber">SAMPLE</Pill> : <Pill tone="green">Live</Pill>}{i.delisted_on && <Pill tone="red">Delisted</Pill>}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </div>
            {pages > 1 && (
              <nav aria-label="Pagination" className="mt-3 flex items-center justify-center gap-2 text-sm">
                <button className="btn-ghost" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>← Prev</button>
                <span className="num text-muted">Page {page} / {pages}</span>
                <button className="btn-ghost" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>Next →</button>
              </nav>
            )}
          </>
        )
      )}
      <Disclaimer />
    </>
  );
}

export default function StocksPage() {
  return (
    <Suspense>
      <StocksInner />
    </Suspense>
  );
}
