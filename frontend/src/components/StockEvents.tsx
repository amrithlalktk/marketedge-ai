"use client";

import { api } from "@/lib/api";
import { moveClass, num, pct, signedPct } from "@/lib/format";
import type { StockEvents } from "@/lib/types";
import { useApi } from "@/lib/useApi";
import { EventRow, NewsCard } from "./NewsEvents";
import { Card, EmptyState, ErrorState, Pill, Skeleton, Stat, TableWrap, cx } from "./ui";

export function StockEventsSection({ symbol }: { symbol: string }) {
  const q = useApi<StockEvents>(() => api.stocks.events(symbol), [symbol]);
  if (q.error) return <Card title="Events & news"><ErrorState error={q.error} onRetry={q.reload} what="events" /></Card>;
  if (!q.data) return <Card title="Events & news"><Skeleton className="h-40" /></Card>;
  const d = q.data;
  const ne = d.next_earnings;
  const r = d.earnings_reaction;
  const now = Date.now();
  return (
    <section aria-labelledby="ev-h" className="space-y-4">
      <h2 id="ev-h" className="text-base font-semibold">Events &amp; news</h2>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Card title="Earnings">
          {!ne ? <p className="text-sm text-muted">{d.market === "CRYPTO" || d.market === "FX" ? "No earnings for this instrument type." : "No upcoming earnings date on record."}</p> : (
            <div className={cx("rounded border p-2 text-sm", ne.warning ? "border-amber-800 bg-amber-950/30" : "border-edge bg-panel2/40")}>
              <p className="font-semibold">Next report: <span className="num">{ne.event_date}</span> ({ne.time === "bmo" ? "before open" : ne.time === "amc" ? "after close" : ne.time}) · {ne.period}</p>
              <p className="text-xs text-muted">in {ne.days_until} days · EPS est. <span className="num text-ink">{num(ne.eps_estimate, 2)}</span> · revenue est. <span className="num text-ink">{num(ne.revenue_estimate, 1)}</span></p>
              {ne.warning && <p className="mt-1 font-semibold text-amber-300">{ne.warning}</p>}
            </div>
          )}
          {r && r.count > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              <Stat label="Avg move on report" value={pct(r.avg_abs_move_pct, 2)} sub={`n=${r.count}`} />
              <Stat label="Max move" value={pct(r.max_abs_move_pct, 2)} />
              <Stat label="Typical 2-day move" value={pct(r.typical_2d_move_pct, 2)} sub={r.vs_typical != null ? `${num(r.vs_typical, 2)}× typical` : undefined} />
              <Stat label="Up reactions" value={`${r.up_moves}/${r.count}`} />
            </div>
          )}
          {r && <p className="mt-1 text-[11px] text-muted">{r.note}</p>}
          {d.earnings_history.length > 0 && (
            <div className="mt-3">
              <TableWrap label="Earnings history">
                <table className="tbl min-w-[560px] text-xs">
                  <thead><tr><th scope="col">Date</th><th scope="col">Period</th><th scope="col" className="text-right">EPS est / act</th><th scope="col" className="text-right">EPS surprise</th><th scope="col" className="text-right">Revenue est / act</th><th scope="col" className="text-right">Rev. surprise</th><th scope="col">Guidance</th></tr></thead>
                  <tbody>
                    {d.earnings_history.map((h) => (
                      <tr key={h.event_date}>
                        <td className="num whitespace-nowrap">{h.event_date}</td><td>{h.period}</td>
                        <td className="num text-right">{num(h.eps_estimate, 2)} / {num(h.eps_actual, 2)}</td>
                        <td className={cx("num text-right font-semibold", moveClass(h.eps_surprise_pct))}>{signedPct(h.eps_surprise_pct)}</td>
                        <td className="num text-right">{num(h.revenue_estimate, 0)} / {num(h.revenue_actual, 0)}</td>
                        <td className={cx("num text-right font-semibold", moveClass(h.revenue_surprise_pct))}>{signedPct(h.revenue_surprise_pct)}</td>
                        <td>{h.guidance ? <Pill tone={h.guidance === "raised" ? "green" : h.guidance === "lowered" ? "red" : "slate"}>{h.guidance}</Pill> : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </TableWrap>
            </div>
          )}
          {d.notes.earnings && <p className="mt-2 text-[11px] text-muted">ⓘ {d.notes.earnings}</p>}
        </Card>
        <Card title="Relevant economic events">
          {d.economic_events.length === 0 ? <EmptyState title="No relevant releases in the window" /> : <ul className="divide-y divide-edge">{d.economic_events.map((e) => <EventRow key={`${e.name}-${e.event_time}`} e={e} now={now} />)}</ul>}
          <p className="mt-2 text-[11px] text-muted">Times in IST; highlighted = within 24h.</p>
        </Card>
      </div>
      <Card title={`Recent news (${d.news.length})`}>
        {d.news.length === 0 ? <EmptyState title="No recent articles for this instrument" /> : <div className="divide-y divide-edge">{d.news.map((n) => <NewsCard key={n.id} n={n} compact />)}</div>}
        {d.notes.news && <p className="mt-2 rounded border border-blue-900 bg-blue-950/30 px-2 py-1 text-[11px] text-blue-100">ⓘ {d.notes.news}</p>}
      </Card>
    </section>
  );
}
