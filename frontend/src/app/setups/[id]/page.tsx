"use client";

import { featureOn } from "@/lib/market";

import Link from "next/link";
import { useParams } from "next/navigation";
import { PositionSizer } from "@/components/PositionSizer";
import { ScoreBadge } from "@/components/SetupCard";
import { ChecksPanel, ExamplesTable, GeneratedStamp, LevelsPanel, MtfPanel, ProbabilityPanel, ScoreBreakdown, WhyPanel } from "@/components/SetupSections";
import { Card, Collapsible, DataStamp, DirectionBadge, Disclaimer, ErrorState, Loading, Pill, Stat, StatusBadge, UpgradeNote } from "@/components/ui";
import { api } from "@/lib/api";
import { isFx, moveClass, pct, price, rr, signedPct } from "@/lib/format";
import { marketLabel } from "@/lib/market";
import { DerivativesPanel, InrPanel, PipsPanel } from "@/components/MarketBlocks";
import { EventRiskBlock, NewsFlowBlock } from "@/components/NewsEvents";
import { AnalystPanel } from "@/components/AnalystPanel";
import { MlPanel } from "@/components/MlBlocks";
import { SetupActions } from "@/components/SetupActions";
import { useApi } from "@/lib/useApi";

export default function SetupDetailPage() {
  const { id } = useParams<{ id: string }>();
  const q = useApi(() => api.signals.get(id), [id]);

  if (q.error?.status === 403) return <><UpgradeNote feature="Viewing NO TRADE candidates" /><Disclaimer /></>;
  if (q.error) return <><ErrorState error={q.error} onRetry={q.reload} what="setup" /><Disclaimer /></>;
  if (q.loading || !q.data) return <Loading label="Loading setup…" />;
  const s = q.data;
  const blocking = s.checks.filter((c) => !c.passed && c.severity === "block");
  const o = { fx: isFx(s.market), ref: s.current_price };

  return (
    <>
      <nav aria-label="Breadcrumb" className="mb-2 text-xs text-muted">
        <Link href="/setups" className="link">Setups</Link> / <span>{s.symbol}</span>
      </nav>
      <header className="card mb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="font-mono text-xl font-bold">{s.symbol}</h1>
              <DirectionBadge d={s.direction} />
              <StatusBadge s={s.status} />
              <Pill tone="blue">{s.strategy.name}</Pill>
            </div>
            <p className="mt-0.5 truncate text-sm text-muted">{s.name} · {s.exchange}{s.sector ? ` · ${s.sector}` : ""}</p>
            <p className="text-xs text-muted">{marketLabel(s.market)} · exchange {s.exchange} · prices in {s.currency}</p>
            <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
              <DataStamp meta={s.data} asOf={s.as_of} />
              <GeneratedStamp s={s} />
            </div>
          </div>
          <ScoreBadge score={s.score} label={s.score_label} />
        </div>
        {s.status !== "VALID" && (
          <div role="alert" className="mt-3 rounded border border-amber-800 bg-amber-950/40 p-2 text-sm">
            <p className="font-bold text-amber-200">NO TRADE — this candidate failed validation and is not a trade setup.</p>
            {blocking.length > 0 && (
              <ul className="mt-1 text-xs text-amber-100/90">
                {blocking.map((c) => <li key={c.name}>✗ {c.name}: {c.detail}</li>)}
              </ul>
            )}
          </div>
        )}
        {s.strategy.notes && <p className="mt-3 rounded border border-edge bg-panel2/60 px-2 py-1.5 text-xs text-muted">ⓘ {s.strategy.notes}</p>}
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
          <Stat label="Price" value={price(s.current_price, s.currency, o)} />
          <Stat label="Entry zone" value={`${price(s.entry_zone[0], s.currency, o)}–${price(s.entry_zone[1], s.currency, o)}`} />
          <Stat label="Stop" value={price(s.stop, s.currency, o)} valueClass="text-down" sub={`risk ${pct(s.risk_pct, 2)}`} />
          <Stat label="T1 / T2" value={<><span className="text-up">{price(s.targets[0], s.currency, o)}</span> / <span className="text-up">{price(s.targets[1], s.currency, o)}</span></>} sub={`R:R ${rr(s.rr_t1)} / ${rr(s.rr_t2)}`} />
          <Stat label="Historical T1 hit rate" value={s.probability.sample_size ? pct(s.probability.t1_hit_rate) : "n/a"} sub={s.probability.sample_size ? `n=${s.probability.sample_size}` : "insufficient history"} />
          <Stat label="Typical hold" value={s.expected_holding_days != null ? `${Math.round(s.expected_holding_days)} sessions` : "—"} sub={`Trend ${s.trend} · Mom ${s.momentum} · Vol ${s.volume}`} />
        </div>
        {s.outcome && (
          <p className="mt-3 text-xs text-muted">
            Forward-tracked outcome: <strong className="text-ink">{s.outcome.status}</strong>
            {s.outcome.exit_reason && <> · exit {s.outcome.exit_reason}</>}
            {s.outcome.net_return_pct != null && <> · <span className={moveClass(s.outcome.net_return_pct)}>{signedPct(s.outcome.net_return_pct)}</span></>}
          </p>
        )}
        <p className="mt-3 text-xs"><Link href={`/stocks/${encodeURIComponent(s.symbol)}`} className="link">Open chart & full analysis for {s.symbol} →</Link></p>
      </header>

      {(s.inr || s.pips || s.derivatives) && (
        <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {s.inr && <InrPanel i={s.inr} currency={s.currency} />}
          {s.pips && <PipsPanel p={s.pips} />}
          {s.derivatives && <div className={s.inr && !s.pips ? "" : "lg:col-span-2"}><DerivativesPanel d={s.derivatives} marketCap={s.market_cap_usd} spreadBps={s.spread_bps} exchange={s.exchange} /></div>}
        </div>
      )}
      {(s.events || s.news) && (
        <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
          {s.events && featureOn("calendar") && <EventRiskBlock ev={s.events} checks={s.checks} />}
          {s.news && featureOn("news") && <NewsFlowBlock n={s.news} />}
        </div>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <LevelsPanel s={s} />
        <ScoreBreakdown components={s.components} score={s.score} label={s.score_label} notes={s.score_notes} />
      </div>
      <div className="mt-4"><WhyPanel e={s.explanation} /></div>
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="space-y-4">
        <ProbabilityPanel p={s.probability} strategyRules={{ entry: s.strategy.entry_rule, exit: `${s.strategy.stop_rule} ${s.strategy.target_rule} Time exit after ${s.strategy.max_hold_bars} bars.` }} />
        {featureOn("ml") && <MlPanel ml={s.ml} />}
        </div>
        <ChecksPanel checks={s.checks} />
      </div>
      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <MtfPanel m={s.mtf} />
        {s.market_regime && (
          <Card title="Market regime at scan">
            <p className="text-sm"><Pill tone={s.market_regime.family === "bull" ? "green" : s.market_regime.family === "bear" ? "red" : "amber"}>{s.market_regime.regime}</Pill> <span className="text-xs text-muted">volatility {s.market_regime.volatility}</span></p>
            <ul className="mt-2 space-y-0.5 text-xs text-muted">{s.market_regime.reasons.map((r) => <li key={r}>• {r}</li>)}</ul>
          </Card>
        )}
      </div>
      <div className="mt-4"><ExamplesTable rows={s.historical_examples} currency={s.currency} fx={o.fx} /></div>
      <p className="mt-2 text-[11px] text-muted">{s.explanation.historical_basis}</p>

      <div className="mt-4"><SetupActions s={s} signalId={s.id ?? Number(id)} /></div>
      {featureOn("analyst") && <div className="mt-4"><AnalystPanel signalId={s.id ?? Number(id)} /></div>}
      <div className="mt-4 space-y-3">
        <Collapsible title="Position-size quick calc" defaultOpen>
          <PositionSizer entry={s.current_price} stop={s.stop} atr={s.atr} direction={s.direction} compact={false} currency={s.currency} />
        </Collapsible>
        <Collapsible title="Strategy rules">
          <p className="text-sm">{s.strategy.description}</p>
          <ul className="mt-2 space-y-0.5 text-xs text-muted">{s.strategy.conditions.map((c) => <li key={c}>• {c}</li>)}</ul>
          <dl className="mt-2 space-y-1 text-xs">
            <div><dt className="inline text-muted">Entry: </dt><dd className="inline">{s.strategy.entry_rule}</dd></div>
            <div><dt className="inline text-muted">Stop: </dt><dd className="inline">{s.strategy.stop_rule}</dd></div>
            <div><dt className="inline text-muted">Targets: </dt><dd className="inline">{s.strategy.target_rule}</dd></div>
          </dl>
        </Collapsible>
        <Collapsible title="Indicator snapshot">
          <dl className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
            {Object.entries(s.indicators).map(([k, v]) => (
              <div key={k} className="rounded border border-edge px-2 py-1"><dt className="text-muted">{k}</dt><dd className="num">{v == null ? "—" : v}</dd></div>
            ))}
          </dl>
        </Collapsible>
      </div>
      <Disclaimer />
    </>
  );
}
