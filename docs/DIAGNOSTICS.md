# Signal diagnostics and strategy validation

This document explains why the NSE stock signals were losing, what was changed, and how strategies are now validated.
Every number below was **measured** on the app's stored NSE daily history. The data covers 456 stocks; the trades run
from 2 May 2023 to 9 Oct 2026, and the measurements were taken on 9 Oct 2026. Nothing below is an illustration.
The live pages recompute the same figures after every daily scan: **Diagnostics** and **Track record**.

## How to reproduce

- **Losing-trade diagnostics** (read-only):
  - go to GitHub → Actions → `daily` → Run workflow, and choose `DIAGNOSE`;
  - or run `python -m app.cli diagnose --market NSE` against the database.
- **Inspecting a single scan:** choose `REPORT` instead.
- **What every daily scan stores:**
  - each strategy's validation status (snapshot `validation`), shown on the Diagnostics page;
  - the diagnostics of the live rules (snapshot `diagnostics`).
- **Where the code lives:**
  - `engine/diagnostics.py`: the gate replay, outcome and loss statistics, and the corporate-action audit;
  - `engine/acceptance.py`: strategy validation;
  - `engine/candidates.py`: the version-2 strategies under test, which are not live.

## Method

- **Trade replay:** every strategy is replayed over the history with the live rules:
  - the signal is taken on the daily close;
  - entry is at the next session's open, and is skipped if the open gaps more than 0.5 ATR or opens through the stop;
  - when one day touches both the stop and a target, the stop is assumed first (conservative), and the trade is flagged as ambiguous;
  - costs and slippage are charged on every fill;
  - there is at most one open position per symbol and strategy.
- **Gate replay:** this walks the history in time order and asks whether the publish gate would have shown each trade on its signal date. It uses only trades that had exited before that date, so there is no look-ahead.
- **Design and out-of-sample split:**
  - Design: 2 May 2023 – 22 May 2025, the first 60% of the history.
  - Out-of-sample: 23 May 2025 – 9 Oct 2026, the last 40%.
  - Gate policies and the stop-distance change were chosen on the design period, then measured once on the out-of-sample period.

## 1. Why signals were losing (measured)

**All historical trades of the 9 live strategies (12,060 trades):**

| Metric | Value |
|---|---|
| Reached Target 1 | 30.7% |
| Hit the stop first | 44.1% |
| Time exit | 25.1% |
| Average result per trade | **−0.054 R** (± 0.011) |
| Profit factor | 0.90 |
| Average return per trade, before costs | +0.05% |
| Average return per trade, after costs | −0.24% |

**Average result by year:**

| Year | Average result |
|---|---|
| 2023 | +0.204 R |
| 2024 | −0.046 R |
| 2025 | −0.129 R |
| 2026 | −0.197 R |

**Average result by regime:**

| Regime | Average result |
|---|---|
| Strong Bull | +0.056 R |
| Weak Bull | −0.021 R |
| Range | −0.085 R |
| Bear | −0.300 R |
| Panic | −0.564 R |

**Average result by setup score:**

| Score | Average result |
|---|---|
| 75+ | −0.066 R |
| 60–75 | −0.062 R |
| Under 60 | −0.001 R |

**What the old gate would have published (3,811 trades):**

| Period | Hit the stop first | Average result |
|---|---|---|
| All | 46.9% | −0.047 R |
| Out-of-sample | 47.7% | **−0.086 R** |

The trades it rejected did worse (−0.176 R out-of-sample). So the gate filtered in the right direction, but what it passed still lost money.

**Calibration:** the app would have shown an average 36% chance of reaching Target 1 for out-of-sample trades. 29% actually did, so the shown chance was **overstated by about 7 points**.

**Causes, in order of evidence:**

1. **The edge decayed.** Every strategy that was profitable in the design period lost money out of sample (see the table in section 3). The gate pooled all history, so the strong 2023 kept approving strategies that lose today.
   - The pool is today's 300 most-traded stocks, which adds survivorship and selection bias to the early years.
2. **The regime filter was too weak.** Long setups were only clearly positive in Strong Bull markets. Range, Bear and Recovery conditions lost.
3. **The setup score does not predict outcomes.** Higher scores did slightly worse than lower ones.
4. **Costs** (about 0.29% per round trip) turn a marginal gross edge negative.
5. **Some strategies lose in every period:**
   - all three short setups;
   - squeeze breakout;
   - cup-and-handle.
6. **Tight stops are hit by normal daily price swings:**

   | Stop distance | Stop hit first | Average result |
   |---|---|---|
   | 0.75–1.0 ATR | 68% | −0.28 R |
   | 1.0–1.5 ATR | 57% | −0.09 R |
   | 1.5–2.5 ATR | 45% | −0.02 R |
   | over 2.5 ATR | 29% | +0.03 R |

7. **A defect: entries just above the stop.** When the next open leaves almost no distance to the stop, R becomes meaningless. This affected 58 trades. One of them was recorded as **+171 R**, which distorted every average. It made "breakout retest" look profitable when it is not.

**Ruled out:**

| Possible cause | Finding |
|---|---|
| Unadjusted corporate actions | Only 3 suspected jumps in 4 years: RAYMOND and SIEMENS (both look like demergers) and INDIAGLYCO. |
| Same-day stop-and-target ambiguity | 0.1% of trades. |
| Look-ahead | Pivots are aligned to the bar that confirms them, weekly trends use completed weeks, and the probability only uses earlier exits. These are covered by existing tests. |
| Tracker errors | It uses the same simulator as the backtest, and an integration test checks that they agree. |

## 2. What changed

| Change | Why | Effect |
|---|---|---|
| **Strategy validation (out-of-sample)** is a blocking check on every live setup (NSE, crypto and NIFTY index setups). | Cause 1 | A strategy publishes only if its last 40% of history passes the acceptance rules (below). |
| Setups that fail **only** validation become **paper trades**: tracked on Track record → Paper, never published or messaged as ideas. | The document's "UNVALIDATED → paper trading" rule | Forward evidence accumulates without recommending trades. |
| Entries are skipped when the next open leaves less than 0.5 ATR to the stop (`min_fill_risk_atr`). This applies to the backtest, the tracker and the entry rule shown to users. | Cause 7 | Removes the meaningless R outliers. |
| A structural stop is used only when it is at least **1.5 ATR** away (was 0.8 ATR); otherwise the stop is 2×ATR. | Cause 6 | Predeclared test; see below. |
| The probability carries out-of-sample results and a **calibration** status. | Section 8 of the document | The shown chance states how accurate it has been. |
| The **Diagnostics** page shows the following, refreshed by every daily scan: | Section 10 of the document | |
|  | | results by strategy, regime, score, year, exit and sector; |
|  | | how stopped trades failed, and average favourable and adverse moves; |
|  | | performance before and after costs; |
|  | | data checks; |
|  | | the gate comparison; |
|  | | the live track record. |

Published stops and targets are stored once and never rewritten. Outcomes are only ever added.

### Acceptance rules

All of these are configurable in `ValidationConfig`. The thresholds are initial screening values, not proof that a strategy is reliable.

- At least **100** out-of-sample trades.
- Out-of-sample average result minus 1 standard error must be **above zero** after costs.
- Out-of-sample profit factor of at least **1.1**.
- Out-of-sample maximum drawdown of at most **40 R**.
- The design period must also be profitable.
- At least half of the calendar years must be profitable.

### Stop-distance test (predeclared)

| Strategy | Stop rule | Design avg | Out-of-sample avg | Out-of-sample stop rate |
|---|---|---|---|---|
| Trend pullback | 0.8 ATR | +0.118 | −0.107 | 45.5% |
| | 1.5 ATR | +0.133 | −0.083 | 43.0% |
| Trend pullback v2 | 0.8 ATR | +0.089 | −0.118 | 47.4% |
| | 1.5 ATR | +0.099 | −0.097 | 44.8% |
| Support bounce | 0.8 ATR | −0.024 | −0.128 | 51.3% |
| | 1.5 ATR | +0.017 | −0.064 | 46.5% |

- The wider stop improved results in both periods, so it is not a one-period fluke.
- The gains are small (about one standard error), and **no strategy becomes profitable**.
- Breakout and relative-strength strategies were unchanged, because their stops were already wider.

## 3. The four strategies from the patch (version 2, measured)

| Strategy | Design avg (R) | Out-of-sample avg (R) | Verdict |
|---|---|---|---|
| A. Breakout retest | +0.157 | −0.064 | UNVALIDATED |
| B. Trend pullback v2 (rising 50 EMA, 20/50 EMA pullback, reversal bar) | +0.089 | −0.118 | UNVALIDATED |
| C. Relative-strength leader (vs NIFTY, 20/60/120 days) | +0.073 | −0.024 | UNVALIDATED |
| D. Support reversal v2 (wick rejection, volume) | +0.013 | −0.226 (only 70 trades) | UNVALIDATED |

The breakout-retest figure is the value after removing near-stop entries. Before that fix, one +171 R outlier made it look like +0.25 R.

**Live strategies, out of sample:**

| Strategy | Out-of-sample avg (R) |
|---|---|
| Breakout + volume | −0.055 |
| Trend pullback | −0.107 |
| Squeeze breakout | −0.172 |
| Support bounce | −0.128 |
| Cup-and-handle | −0.285 |
| Geometric breakout | −0.286 (55 trades) |
| Bear pullback | −0.191 |
| Breakdown + volume | −0.345 |

**Result: all 13 strategies are UNVALIDATED on NSE as of 9 Oct 2026.** None of the alternative publish gates was profitable out of sample either:
- requiring at least 100 comparable trades;
- requiring a positive average after subtracting one or 1.64 standard errors;
- using regime-only evidence cells;
- adding a "last 12 months positive" rule (out-of-sample −0.20 R, worse).

As the patch requires, the app now publishes **NO TRADE** for NSE stocks rather than inventing ideas. Setups are tracked as paper trades. A strategy is re-evaluated by every scan and goes live automatically if its recent out-of-sample record passes.

## Limitations

- **Survivorship and selection:** the NSE pool is today's most-traded stocks. Delisted and shrunken companies are missing, which flatters the early years.
- **No sector data:** the data source has no stock-to-sector map, so Strategy C uses NIFTY 50 only, and the results-by-sector table shows "Unknown".
- **Partial gate replay:** the replay reproduces the evidence, score, R:R, stop-width, target-distance and regime rules. The liquidity, data-quality, volatility and multi-timeframe checks are not replayed.
- **No intraday history:** there is no intraday history for stocks, so same-day stop-and-target order cannot be resolved; the stop is assumed (0.1% of trades).
- **Too few live results:** the live track record started on 5 Oct 2026 and is too small to judge.
