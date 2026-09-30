"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { CompareTable } from "@/components/BacktestExtras";
import { BacktestResults } from "@/components/BacktestResults";
import { StrategyBuilder } from "@/components/StrategyBuilder";
import { StrategyPerf } from "@/components/StrategyPerf";
import { MarketSwitcher } from "@/components/MarketSwitcher";
import { marketLabel, useMarket } from "@/lib/market";
import { Card, Disclaimer, EmptyState, ErrorState, Field, InlineError, Loading, NumberInput, PageHeader, Pill, Segmented, UpgradeNote, cx } from "@/components/ui";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { defaultState, fromDefinition, toDefinition, type BuilderState } from "@/lib/dsl";
import { dateTime, moveClass, num } from "@/lib/format";
import type { Backtest, BacktestIn, BacktestListItem, CompareItem, StrategiesResponse, StrategyDefinitionV2 } from "@/lib/types";
import { useApi } from "@/lib/useApi";

type Mode = "saved" | "builder";

function BacktestInner() {
  const { can } = useAuth();
  const params = useSearchParams();
  const router = useRouter();
  const canRun = can("backtests:run");
  const canManage = can("strategies:manage");
  const strat = useApi<StrategiesResponse>(() => api.strategies.list(), []);
  const history = useApi<{ items: BacktestListItem[] }>(() => api.backtests.list(30), [], canRun);

  const [mode, setMode] = useState<Mode>(params.get("mode") === "builder" || params.get("load") ? "builder" : "saved");
  const [key, setKey] = useState(params.get("strategy") ?? "");
  const [version, setVersion] = useState(params.get("version") ?? "");
  const [builder, setBuilder] = useState<BuilderState>(defaultState);
  const [saveKey, setSaveKey] = useState("");
  const [saveNote, setSaveNote] = useState("");
  const [saveMsg, setSaveMsg] = useState<string | null>(null);
  const [market] = useMarket();
  const isFxM = market === "FX";
  const [marketCosts, setMarketCosts] = useState(true);
  const [commission, setCommission] = useState("0.12");
  const [slippage, setSlippage] = useState("0.05");
  const [risk, setRisk] = useState("1");
  const [partial, setPartial] = useState("50");
  const [maxHold, setMaxHold] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [wf, setWf] = useState(true);
  const [trainY, setTrainY] = useState("3");
  const [testY, setTestY] = useState("1");
  const [pfOn, setPfOn] = useState(true);
  const [capital, setCapital] = useState("1000000");
  const [maxPos, setMaxPos] = useState("10");
  const [maxPosPct, setMaxPosPct] = useState("20");
  const [sectorPct, setSectorPct] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [current, setCurrent] = useState<Backtest | null>(null);
  const [pollErr, setPollErr] = useState<Error | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [compare, setCompare] = useState<{ items: CompareItem[]; note: string } | null>(null);
  const [compareErr, setCompareErr] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const resultRef = useRef<HTMLElement>(null);

  // FX uses price-only (px_*) rules that are not in the built-in list; equity-type built-ins need volume.
  const builtin = isFxM ? [] : strat.data?.builtin ?? [];
  const custom = (strat.data?.custom ?? []).filter((c) => c.is_active);
  const selectedKey = key || builtin[0]?.id || (strat.data?.custom ?? []).find((c) => c.is_active)?.id || "";
  const selectedCustom = custom.find((c) => c.id === selectedKey);

  // Keep the form in sync when the URL changes client-side (e.g. "Backtest v2" links while already on /backtest).
  const qStrategy = params.get("strategy");
  const qVersion = params.get("version");
  const qMode = params.get("mode");
  useEffect(() => {
    if (qStrategy) {
      setKey(qStrategy);
      setVersion(qVersion ?? "");
      setMode("saved");
    }
  }, [qStrategy, qVersion]);
  useEffect(() => {
    if (qMode === "builder") setMode("builder");
  }, [qMode]);

  // ?load=key opens the builder with that strategy's current definition
  const loadKey = params.get("load");
  const loaded = useRef(false);
  useEffect(() => {
    loaded.current = false;
  }, [loadKey]);
  useEffect(() => {
    if (!loadKey || loaded.current || !strat.data) return;
    const c = strat.data.custom.find((x) => x.id === loadKey);
    if (c) {
      loaded.current = true;
      setMode("builder");
      setBuilder(fromDefinition(c.definition));
    }
  }, [loadKey, strat.data]);

  const reloadHistory = history.reload;
  const poll = useCallback((id: number) => {
    if (timer.current) clearTimeout(timer.current);
    const tick = async () => {
      try {
        const bt = await api.backtests.get(id);
        setCurrent(bt);
        setPollErr(null);
        if (bt.status === "queued" || bt.status === "running") timer.current = setTimeout(tick, 2000);
        else reloadHistory();
      } catch (e) {
        setPollErr(e instanceof Error ? e : new Error(String(e)));
      }
    };
    tick();
  }, [reloadHistory]);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  function common(): BacktestIn {
    return {
      market,
      ...(marketCosts ? {} : { commission_pct: Number(commission), slippage_pct: Number(slippage) }),
      risk_per_trade_pct: Number(risk),
      partial_at_t1: Math.min(1, Math.max(0, Number(partial) / 100)),
      walk_forward: wf,
      ...(wf ? { train_years: Number(trainY), test_years: Number(testY) } : {}),
      ...(start ? { start } : {}),
      ...(end ? { end } : {}),
      ...(pfOn ? { portfolio: { initial_capital: Number(capital), max_positions: Math.round(Number(maxPos)), max_position_pct: Number(maxPosPct), ...(sectorPct ? { max_sector_pct: Number(sectorPct) } : {}) } } : {}),
    };
  }

  async function run(body: BacktestIn) {
    setError(null);
    setBusy(true);
    try {
      const res = await api.backtests.create(body);
      setCurrent({ id: res.id, status: res.status as Backtest["status"], params: {}, result: null, error: null, created_at: "", finished_at: null, disclaimer: "" });
      setCompare(null);
      poll(res.id);
      setTimeout(() => resultRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }), 100);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start backtest");
    } finally {
      setBusy(false);
    }
  }

  const runSaved = (e: React.FormEvent) => {
    e.preventDefault();
    const body = common();
    body.strategy_key = selectedKey;
    if (selectedCustom && version) body.strategy_version = Number(version);
    if (maxHold) body.max_hold_bars = Number(maxHold);
    run(body);
  };
  const runAdhoc = (d: StrategyDefinitionV2) => run({ ...common(), definition: d });

  async function saveAsStrategy(d: StrategyDefinitionV2) {
    setSaveMsg(null);
    setError(null);
    if (!/^[a-z0-9_]{3,64}$/.test(saveKey)) return setError("Strategy key: 3–64 characters, lowercase letters, digits or underscore.");
    try {
      const c = await api.strategies.create(saveKey, d, saveNote);
      setSaveMsg(`Saved “${c.name}” as ${c.id} v${c.current_version}.`);
      strat.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    }
  }

  async function runCompare() {
    setCompareErr(null);
    try {
      setCompare(await api.backtests.compare(selected));
      setTimeout(() => document.getElementById("compare")?.scrollIntoView({ behavior: "smooth" }), 50);
    } catch (e) {
      setCompareErr(e instanceof Error ? e.message : "Compare failed");
    }
  }

  const setModeUrl = (m: Mode) => { setMode(m); router.replace(m === "builder" ? "/backtest?mode=builder" : "/backtest", { scroll: false }); };
  const running = current?.status === "queued" || current?.status === "running";

  return (
    <>
      <PageHeader title={`Backtest — ${marketLabel(market)}`} subtitle="Test a strategy on history — strategy-level R statistics, walk-forward validation and a capital-constrained portfolio simulation." right={<div className="flex flex-col items-end gap-1"><MarketSwitcher /><Link href="/strategies" className="link text-sm">Manage saved strategies →</Link></div>} />
      {isFxM && (
        <p role="note" className="mb-3 rounded border border-blue-900 bg-blue-950/30 px-3 py-2 text-xs text-blue-100">
          ⓘ Forex has no consolidated volume, so FX is scanned with price-only rules (the <code className="font-mono">px_*</code> strategies), which are not in the built-in list. Backtest FX with a custom rule set that avoids volume features (e.g. <code className="font-mono">volume</code>, <code className="font-mono">vol_ratio20</code>, <code className="font-mono">obv</code>) or a saved custom strategy.
        </p>
      )}

      {!canRun ? <UpgradeNote feature="Running backtests" /> : (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
          <div className="min-w-0 space-y-4 xl:col-span-2">
            <Card title="Strategy">
              {strat.error && <ErrorState error={strat.error} onRetry={strat.reload} what="strategies" />}
              <div className="mb-3"><Segmented<Mode> label="Strategy source" value={mode} onChange={setModeUrl} options={[{ value: "saved", label: "Built-in / saved strategy" }, { value: "builder", label: "Rule builder" }]} /></div>
              {mode === "saved" ? (
                <form onSubmit={runSaved} noValidate className="space-y-3">
                  <div className="grid grid-cols-1 gap-3 sm:grid-cols-[1fr_10rem]">
                    <Field label="Strategy" htmlFor="strategy">
                      <select id="strategy" className="input" value={selectedKey} onChange={(e) => { setKey(e.target.value); setVersion(""); }}>
                        {builtin.length > 0 && <optgroup label="Built-in">{builtin.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.direction})</option>)}</optgroup>}
                        {!selectedKey && <option value="">No strategy available — use the rule builder</option>}
                        {custom.length > 0 && <optgroup label="Custom (saved)">{custom.map((c) => <option key={c.id} value={c.id}>{c.name} — {c.id} ({c.direction})</option>)}</optgroup>}
                      </select>
                    </Field>
                    {selectedCustom ? (
                      <Field label="Version" htmlFor="ver">
                        <select id="ver" className="input" value={version} onChange={(e) => setVersion(e.target.value)}>
                          <option value="">current (v{selectedCustom.current_version})</option>
                          {[...selectedCustom.versions].reverse().map((v) => <option key={v.version} value={v.version}>v{v.version}{v.note ? ` — ${v.note.slice(0, 30)}` : ""}</option>)}
                        </select>
                      </Field>
                    ) : <NumberInput label="Max hold override" suffix="bars" value={maxHold} onChange={setMaxHold} min={1} max={120} step="1" />}
                  </div>
                  {selectedCustom && <p className="text-xs text-muted">{selectedCustom.conditions.join(" AND ")} · <Link className="link" href={`/backtest?load=${encodeURIComponent(selectedCustom.id)}`}>open in builder</Link></p>}
                  <InlineError error={error} />
                  <button className="btn-primary" disabled={busy || running || !strat.data || !selectedKey}>{busy ? "Submitting…" : `Run backtest on ${marketLabel(market)}`}</button>
                </form>
              ) : (
                <div className="space-y-3">
                  <StrategyBuilder state={builder} setState={setBuilder} features={strat.data?.allowed_features ?? []} operators={strat.data?.operators ?? [">", ">=", "<", "<=", "crosses_above", "crosses_below"]} busy={busy || running}
                    actions={[{ label: busy ? "Submitting…" : "Backtest now", primary: true, onClick: runAdhoc }]} />
                  {canManage ? (
                    <fieldset className="rounded border border-edge p-3">
                      <legend className="px-1 text-xs text-muted">Save as a versioned strategy</legend>
                      <div className="grid grid-cols-1 gap-3 sm:grid-cols-[14rem_1fr_auto] sm:items-end">
                        <Field label="Key (a-z, 0-9, _)" htmlFor="sv-key"><input id="sv-key" className="input font-mono" maxLength={64} value={saveKey} onChange={(e) => setSaveKey(e.target.value.toLowerCase())} placeholder="my_breakout_v2" /></Field>
                        <Field label="Version note" htmlFor="sv-note"><input id="sv-note" className="input" maxLength={500} value={saveNote} onChange={(e) => setSaveNote(e.target.value)} placeholder="Initial version" /></Field>
                        <SaveButton builder={builder} onSave={saveAsStrategy} />
                      </div>
                      {saveMsg && <p role="status" className="mt-2 text-sm text-green-300">{saveMsg} <Link className="link" href={`/strategies?key=${encodeURIComponent(saveKey)}`}>Manage it →</Link></p>}
                    </fieldset>
                  ) : <p className="text-xs text-muted">Saving strategies requires the Analyst role; you can still backtest ad-hoc rule sets.</p>}
                  <InlineError error={error} />
                </div>
              )}
            </Card>

            <Card title="Test settings">
              <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                  <div className="col-span-2 sm:col-span-3 lg:col-span-4">
                    <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={marketCosts} onChange={(e) => setMarketCosts(e.target.checked)} /> Use {marketLabel(market)}&apos;s own cost model (commission + slippage)</label>
                  </div>
                  {!marketCosts && <NumberInput label="Commission" suffix="% per side" value={commission} onChange={setCommission} min={0} max={5} step="0.01" />}
                  {!marketCosts && <NumberInput label="Slippage" suffix="% per side" value={slippage} onChange={setSlippage} min={0} max={5} step="0.01" />}
                  <NumberInput label="Risk per trade" suffix="%" value={risk} onChange={setRisk} min={0.1} max={10} step="0.1" />
                  <NumberInput label="Partial exit at T1" suffix="%" value={partial} onChange={setPartial} min={0} max={100} step="5" />
                  <Field label="Start date" htmlFor="start"><input id="start" type="date" className="input" value={start} onChange={(e) => setStart(e.target.value)} /></Field>
                  <Field label="End date" htmlFor="end"><input id="end" type="date" className="input" value={end} onChange={(e) => setEnd(e.target.value)} /></Field>
                </div>
                <fieldset className="rounded border border-edge p-3">
                  <legend className="px-1 text-xs text-muted">Validation</legend>
                  <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={wf} onChange={(e) => setWf(e.target.checked)} /> Walk-forward + parameter sensitivity</label>
                  {wf && (
                    <div className="mt-2 grid grid-cols-2 gap-3 sm:max-w-sm">
                      <NumberInput label="Train window" suffix="years" value={trainY} onChange={setTrainY} min={0.5} max={15} step="0.5" />
                      <NumberInput label="Test window" suffix="years" value={testY} onChange={setTestY} min={0.25} max={5} step="0.25" />
                    </div>
                  )}
                </fieldset>
                <fieldset className="rounded border border-edge p-3">
                  <legend className="px-1 text-xs text-muted">Portfolio simulation</legend>
                  <label className="inline-flex items-center gap-2 text-sm"><input type="checkbox" checked={pfOn} onChange={(e) => setPfOn(e.target.checked)} /> Simulate a single capital-constrained account</label>
                  {pfOn && (
                    <div className="mt-2 grid grid-cols-2 gap-3 sm:grid-cols-4">
                      <NumberInput label="Starting capital" suffix={market === "NSE" ? "₹" : market === "CRYPTO" || market === "US" ? "$" : "units"} value={capital} onChange={setCapital} min={10000} step="10000" hint={Number(capital) > 0 ? (market === "NSE" ? new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(capital)) : new Intl.NumberFormat("en-US").format(Number(capital))) : undefined} />
                      <NumberInput label="Max positions" value={maxPos} onChange={setMaxPos} min={1} max={100} step="1" />
                      <NumberInput label="Max per position" suffix="% equity" value={maxPosPct} onChange={setMaxPosPct} min={1} max={100} step="1" />
                      <NumberInput label="Sector cap" suffix="% equity" value={sectorPct} onChange={setSectorPct} min={1} max={100} step="1" hint="Optional" />
                    </div>
                  )}
                </fieldset>
              </div>
            </Card>
          </div>

          <Card title="Recent backtests" right={selected.length > 0 ? <span className="text-xs text-muted">{selected.length} selected</span> : null}>
            {history.error ? <ErrorState error={history.error} onRetry={history.reload} what="backtests" /> : !history.data ? <Loading /> : history.data.items.length === 0 ? <EmptyState title="No backtests yet" /> : (
              <>
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <button type="button" className="btn-primary px-2 py-1 text-xs" disabled={selected.length < 2 || selected.length > 5} onClick={runCompare}>Compare selected ({selected.length})</button>
                  {selected.length > 0 && <button type="button" className="btn-ghost px-2 py-1 text-xs" onClick={() => setSelected([])}>Clear</button>}
                  <span className="text-[11px] text-muted">Tick 2–5 finished backtests.</span>
                </div>
                <InlineError error={compareErr} />
                <ul className="max-h-[36rem] divide-y divide-edge overflow-y-auto text-sm">
                  {history.data.items.map((b) => (
                    <li key={b.id} className={cx("flex items-center gap-2 px-1", current?.id === b.id && "bg-panel2")}>
                      <input type="checkbox" aria-label={`Select backtest ${b.id} for comparison`} disabled={b.status !== "done" || (!selected.includes(b.id) && selected.length >= 5)} checked={selected.includes(b.id)}
                        onChange={(e) => setSelected((p) => (e.target.checked ? [...p, b.id] : p.filter((x) => x !== b.id)))} />
                      <button type="button" className="flex min-w-0 flex-1 items-center justify-between gap-2 py-2 text-left hover:bg-panel2" onClick={() => { setCompare(null); poll(b.id); }}>
                        <span className="min-w-0">
                          <span className="block truncate font-medium">#{b.id} {b.strategy_key}{b.strategy_version != null ? ` v${b.strategy_version}` : ""}{b.has_portfolio ? " · portfolio" : ""}</span>
                          <span className="block text-[11px] text-muted">{dateTime(b.created_at)}{b.trades != null && ` · ${b.trades} trades`}</span>
                        </span>
                        <span className="flex shrink-0 flex-col items-end gap-0.5">
                          <Pill tone={b.status === "done" ? "green" : b.status === "failed" ? "red" : "amber"}>{b.status}</Pill>
                          {b.expectancy_r != null && <span className={cx("num text-[11px]", moveClass(b.expectancy_r))}>{num(b.expectancy_r, 3)} R</span>}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Card>
        </div>
      )}

      {compare && (
        <section id="compare" aria-label="Backtest comparison" className="mt-4">
          <Card title={`Comparison (${compare.items.length} backtests)`} right={<button className="btn-ghost px-2 py-1 text-xs" onClick={() => setCompare(null)}>Close</button>}>
            <CompareTable items={compare.items} note={compare.note} />
          </Card>
        </section>
      )}

      {current && (
        <section ref={resultRef} aria-label="Backtest result" className="mt-4 scroll-mt-4" aria-live="polite">
          <h2 className="mb-2 text-base font-semibold">Result #{current.id}</h2>
          {pollErr && <ErrorState error={pollErr} onRetry={() => poll(current.id)} what="backtest status" />}
          {running && <Loading label={`Backtest ${current.status}… walk-forward and portfolio simulation can take a minute.`} />}
          {current.status === "failed" && <ErrorState error={new Error(current.error ?? "Backtest failed")} what="backtest" />}
          {current.status === "done" && current.result && <BacktestResults id={current.id} r={current.result} market={typeof current.params?.market === "string" ? current.params.market : "NSE"} />}
        </section>
      )}

      <section aria-label="Strategies" className="mt-6">
        <h2 className="mb-2 text-base font-semibold">Strategies — historical performance</h2>
        {strat.loading && <Loading />}
        {strat.data && (
          <div className="space-y-2">
            {builtin.map((s) => <StrategyPerf key={s.id} s={s} />)}
            {strat.data.custom.filter((c) => c.is_active).map((c) => <StrategyPerf key={c.id} s={c} custom={{ key: c.id, version: c.current_version, inScan: c.include_in_scan }} />)}
          </div>
        )}
      </section>
      <Disclaimer />
    </>
  );
}

function SaveButton({ builder, onSave }: { builder: BuilderState; onSave: (d: StrategyDefinitionV2) => Promise<void> }) {
  // Saving reuses the builder's definition; the server validates it again.
  const [busy, setBusy] = useState(false);
  return (
    <button type="button" className="btn-ghost" disabled={busy} onClick={async () => {
      setBusy(true);
      try {
        await onSave(toDefinition(builder));
      } finally {
        setBusy(false);
      }
    }}>{busy ? "Saving…" : "Save as strategy"}</button>
  );
}

export default function BacktestPage() {
  return (
    <Suspense>
      <BacktestInner />
    </Suspense>
  );
}
