import Link from "next/link";
import { isFx, num, pct, period, price, px, rr } from "@/lib/format";
import { marketLabel } from "@/lib/market";
import { CardExtras } from "./MarketBlocks";
import { MlLine } from "./MlBlocks";
import type { ProbabilitySummary, SetupSummary } from "@/lib/types";
import { Bar, DataStamp, DirectionBadge, StatusBadge } from "./ui";

/** A historical rate is never shown without its sample size and backtest period. */
export function HitRate({ p, label = "Historical T1 hit rate", compact = false }: { p: Partial<ProbabilitySummary> | null | undefined; label?: string; compact?: boolean }) {
  const n = p?.sample_size ?? 0;
  if (!p || !n || p.t1_hit_rate == null) {
    return (
      <span className="text-xs text-muted">
        {label}: <span className="text-ink">insufficient history</span>
      </span>
    );
  }
  return (
    <span className="text-xs text-muted">
      {label}: <span className="num font-semibold text-ink">{pct(p.t1_hit_rate)}</span>
      {p.t1_ci95 && !compact && <span className="num"> (95% CI {pct(p.t1_ci95[0])}–{pct(p.t1_ci95[1])})</span>}
      <span className="num"> · n={n}</span>
      {p.backtest_period && <span className="num"> · {period(p.backtest_period)}</span>}
    </span>
  );
}

export function ScoreBadge({ score, label }: { score: number; label: string }) {
  const tone = score >= 75 ? "text-up" : score >= 60 ? "text-blue-300" : "text-amber-300";
  return (
    <div className="text-right">
      <div className={`num text-lg font-bold leading-none ${tone}`}>{num(score, 0)}</div>
      <div className="mt-0.5 text-[10px] uppercase tracking-wide text-muted">{label}</div>
    </div>
  );
}

function Lvl({ k, v, cls }: { k: string; v: React.ReactNode; cls?: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-[10px] uppercase tracking-wide text-muted">{k}</dt>
      <dd className={`num break-words text-sm font-medium ${cls ?? ""}`}>{v}</dd>
    </div>
  );
}

export function SetupCard({ s, showBlocking = false }: { s: SetupSummary; showBlocking?: boolean }) {
  const cur = s.currency ?? "INR";
  const fx = isFx(s.market);
  const o = { fx, ref: s.current_price };
  return (
    <article className="card flex min-w-0 flex-col gap-3" aria-labelledby={`setup-${s.id}`}>
      <header className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5">
            <h3 id={`setup-${s.id}`} className="font-mono text-base font-semibold">
              {s.symbol}
            </h3>
            <DirectionBadge d={s.direction} />
            <StatusBadge s={s.status} />
          </div>
          <p className="truncate text-xs text-muted" title={s.name ?? undefined}>
            {s.setup_type}
            {s.sector ? ` · ${s.sector}` : ""}
          </p>
          <p className="text-[11px] text-muted">{marketLabel(s.market)} · {s.exchange ?? s.market} · {cur}</p>
        </div>
        <ScoreBadge score={s.score} label={s.score_label} />
      </header>

      <dl className="grid grid-cols-2 gap-x-3 gap-y-2 min-[400px]:grid-cols-3">
        <Lvl k="Price" v={price(s.current_price, cur, o)} />
        <Lvl k="Entry zone" v={`${px(s.entry_zone[0], o)}–${px(s.entry_zone[1], o)}`} />
        <Lvl k="Stop" v={price(s.stop, cur, o)} cls="text-down" />
        <Lvl k="T1" v={price(s.targets[0], cur, o)} cls="text-up" />
        <Lvl k="T2" v={price(s.targets[1], cur, o)} cls="text-up" />
        <Lvl k="R:R (T1 / T2)" v={`${rr(s.rr_t1)} / ${rr(s.rr_t2)}`} />
      </dl>

      <div className="space-y-1">
        <HitRate p={s.probability} compact />
        <div className="text-xs text-muted">
          Risk {pct(s.risk_pct, 2)} · Reward to T2 {pct(s.reward_pct_t2, 2)} · Typical hold {s.expected_holding_days != null ? `${num(s.expected_holding_days, 0)} sessions` : "—"}
        </div>
        <Bar value={s.score} />
      </div>
      <CardExtras s={s} />
      <MlLine ml={s.ml} />

      {s.reasons.length > 0 && (
        <ul className="space-y-0.5 text-xs">
          {s.reasons.slice(0, 4).map((r) => (
            <li key={r} className="flex gap-1.5">
              <span className="text-up" aria-hidden>✓</span>
              <span className="min-w-0">{r}</span>
            </li>
          ))}
        </ul>
      )}
      {s.risk_factors.length > 0 && (
        <ul className="space-y-0.5 text-xs text-amber-200/90">
          {s.risk_factors.slice(0, 3).map((r) => (
            <li key={r} className="flex gap-1.5">
              <span aria-hidden>⚠</span>
              <span className="min-w-0">{r}</span>
            </li>
          ))}
        </ul>
      )}
      {showBlocking && s.blocking_checks.length > 0 && (
        <div className="rounded border border-amber-900 bg-amber-950/40 p-2 text-xs">
          <p className="font-semibold text-amber-200">Blocked by validation</p>
          <ul className="mt-1 space-y-0.5 text-amber-100/90">
            {s.blocking_checks.map((b) => (
              <li key={b}>✗ {b}</li>
            ))}
          </ul>
        </div>
      )}

      <footer className="mt-auto flex flex-wrap items-center justify-between gap-2 border-t border-edge pt-2">
        <DataStamp meta={s.data} asOf={s.as_of} />
        <Link href={`/setups/${s.id}`} className="link text-xs font-semibold">
          Full analysis →
        </Link>
      </footer>
    </article>
  );
}
