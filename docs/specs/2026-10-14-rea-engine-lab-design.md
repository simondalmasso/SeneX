# REA Engine Lab — Design Specification

- **Status:** Owner-approved scope; implemented in isolated local branch, uncommitted and unpublished
- **Date:** 2026-10-14
- **SENEX baseline:** `main@583e11588cea8594db8625a01dc0305ec826f008`
- **REA source of evidence:** local `trading-bot-rea-20261013/CLEANROOM-ENGINES` and `INDEPENDENT-PAPER-BOT`

## 1. Objective

Bring the most reusable REA work into SENEX as an isolated, reproducible, zero-spend historical research lab. The first strategy family is clean-room Spot Grid, DCA, and Combo, using the existing REA candle-event semantics and explicit accounting costs. The objective is to determine whether any candidate survives fair, cost-aware out-of-sample evaluation—not to assume that the highest historical return will make money.

This is an independent model of publicly documented behavior. It is not a reconstruction of private commercial source code and must not be represented as an exact Bitsgap, Gunbot, or HaasOnline engine.

## 2. Existing-code findings and placement

SENEX already has `backend.execution_simulator.ExecutionSimulator`, `backend.portfolio.ExecutionEngine`, `ExecutionFidelity`, `RiskKernel`, `TradeJournal`, and the isolated `BINANCE_SIM` PAPER lane. The existing execution simulator models signal-to-position execution and costs; it is not the right place to add a separate historical event-replay loop with persistent resting limit orders.

The top-level `research/challengers/` package is currently used for predictor/model experiments. The new code will therefore live in a separate top-level `research/rea_engines/` package and will not be imported by the backend runtime. No production portfolio, predictor, API, scheduler, or BINANCE_SIM code is changed by this design.

## 3. Boundaries and safety invariants

- Offline input only: the caller supplies a local OHLCV CSV. No downloads, network calls, API keys, exchange SDKs, CCXT, account access, or broker adapters.
- Simulation only: zero live orders, zero real capital, zero deployment, zero vendor spend, and no dependency on a connected exchange.
- Spot-only, long-only, cash-backed inventory. No shorts, leverage, margin, futures, liquidation, or borrowing.
- Do not alter frozen ORDER097/098/099/100 evidence, prospective cohorts, current runtime, H011, or any existing order/branch.
- The run report always states `mode=historical-paper`, `network_calls=0`, `live_orders_submitted=0`, and `EDGE=UNPROVEN`. This lab cannot promote SENEX to LIVE or override any lock.
- All strategy behavior is labelled as a clean-room approximation where vendor-private details are unknown.

## 4. Components and interfaces

Proposed files (paths are new and subject to this spec review):

- `research/rea_engines/contracts.py`: immutable candle, portfolio snapshot, order-intent, fill, rejection, and cost-profile contracts.
- `research/rea_engines/accounting.py`: deterministic cash/inventory ledger; weighted average entry; realized and unrealized PnL; fee, spread, slippage, equity, and drawdown accounting. Use `Decimal` for cash, quantities, prices, and fees; round only for report presentation.
- `research/rea_engines/replay.py`: validated OHLCV event loop, pending-order lifecycle, fill/rejection records, mark-to-market equity curve, and run summary.
- `research/rea_engines/strategies.py`: strategy protocol plus Grid, DCA, and Combo clean-room implementations. Strategy instances own only their explicit state and configuration.
- `research/rea_engines/evaluation.py`: fixed-parameter historical comparison, baselines, walk-forward slices, cost stress, and reproducible JSON report.
- `research/rea_engines/cli.py`: optional local-only command taking explicit input CSV, cost profile, strategy config, and output path; no default network or remote path.
- Focused tests under `tests/test_rea_engine_*.py`.

Core interface:
- `Strategy.on_candle(candle, portfolio_snapshot, open_orders) -> list[OrderIntent]`
- `Replay.run(candles, strategy, config) -> RunReport`
- `RunReport` includes source SHA-256, engine/config identity, costs, fills, rejections, pending orders, equity curve, returns, max drawdown, exposure, turnover, and separate fees/spread/slippage totals. Compute `report_sha256` over canonical JSON with sorted keys and normalized Decimal strings, excluding the `report_sha256` field itself.

## 5. Deterministic replay and fill policy

1. Validate every candle's finite positive prices, OHLC ordering, non-negative volume, and strictly increasing timestamps. Reject invalid input instead of repairing it or inventing candles.
2. At each candle, first process orders submitted on earlier candles; then update the portfolio and mark equity at the candle close; only then expose the completed candle and current portfolio to the strategy.
3. A market intent created after candle *t* is eligible at candle *t+1* open, never at the same candle close.
4. A resting buy limit can fill only if a later candle's low reaches its limit; a resting sell limit can fill only if a later candle's high reaches its limit. If that candle opens through the limit, use the open when it is more favorable to the order; otherwise use the limit. No fill is inferred from the signal candle.
5. Limit intents persist across candles until filled, explicitly cancelled, or expired by configured `expires_after_bars`. Orders still open after the final candle are reported as unfilled, never silently counted as fills.
6. Apply the configured fee to each fill. Market fills additionally pay half the configured full spread and configured slippage around the next open. A limit price receives no second spread/slippage surcharge. Every modeled cost is separately reported.
7. If multiple orders compete for cash or inventory on the same candle, process them in stable submission order. Enforce cash, maximum inventory, and maximum fill quantity; reject the excess with a reason code.
8. OHLCV cannot reveal queue priority or the intrabar price path. The report must label these as model assumptions; no order-book fill fidelity is claimed. Any same-candle stop/target ambiguity in future exit logic must use a documented conservative rule.

## 6. Strategy semantics

- **Grid:** spot-only, fixed price range and level count, one resting order at a time. While flat, place the nearest eligible limit buy below the observed close; while holding inventory, place a limit sell at the next grid target above weighted average entry (or the explicit fallback take-profit).
- **DCA:** spot-only initial entry, fixed-quote adverse-move adds, explicit maximum add count, and limit take-profit above weighted average entry. Entry signal, step, order size, target, and add cap are configuration, not hidden optimizer state.
- **Combo:** use Grid for flat-state entries and DCA for management while holding inventory. Do not allow conflicting Grid/DCA orders to suppress adverse-move adds or exits.
- All three consume the same replay and ledger interfaces, the same starting cash, the same cost profile, and the same portfolio limits. Configurations are frozen before the evaluation window; no parameter fitting or optimization on the evaluation window in the first delivery.

The implementation ports the useful behavior and assumptions from REA's own clean-room code, not private vendor logic. Exact commercial engine behavior remains unknown.

## 7. Costs, baselines, and evaluation

- Cost settings are mandatory and versioned. The initial comparison profile will match the existing REA report assumptions: fee 10 bps, full spread 5 bps, slippage 5 bps. These are research assumptions, not a claim about a specific user's actual exchange tier. Also run explicit 2x and 3x cost stress profiles.
- Starting cash and the maximum BTC inventory/fill limits are identical for all candidate strategies and the primary benchmark.
- Report a fixed quote-allocation buy-and-hold benchmark (default 100 USDT, same starting-equity denominator and inventory/fill caps) and a fixed-quantity benchmark separately. Label deployed exposure explicitly; neither is directly comparable to a strategy with materially different exposure without normalization.
- Metrics: ending equity, net return after costs, max drawdown, fees, spread cost, slippage cost, realized/unrealized PnL, fills/rejections/pending orders, turnover, exposure/time-in-market, and cost-stress sensitivity.
- Primary comparison window must reproduce the REA report exactly: 2022-01-01T00:00:00Z through 2026-09-30T20:00:00Z (10,404 4h bars), with earlier candles used only for causal warm-up. Report each calendar-year subperiod inside that window separately, using the same frozen parameters; do not select parameters from those OOS results. No candle from the future is available to a strategy; an order created at a close cannot fill before the next candle.
- Each JSON result records input CSV SHA-256, row count, timestamp range, configuration, engine version, cost profile, fill-policy identifier, benchmark definitions, and report SHA-256. Missing or mismatched input provenance fails closed.
- Synthetic fixtures validate mechanics only; they are never labelled as market-performance evidence.

## 8. Existing REA evidence to preserve

The current REA clean-room comparison uses 14,789 verified Binance Spot BTCUSDT 4h candles (canonical CSV SHA-256 `7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146`), with fixed parameters and modeled costs. It reported SpotGrid-like +3.6384% return / 6.6879% max drawdown / 717 fills; DCA +1.6129% / 1.6474% / 132 fills; Grid +0.2477% / 0.8042% / 2 fills; Combo +0.3543% / 0.6228% / 21 fills. The approximately 100-USDT buy-and-hold benchmark returned +0.8076%; the fixed 0.02 BTC benchmark returned +7.4652% with substantially greater exposure.

These are outputs of independent models under stated assumptions, not commercial-product results, not risk-normalized proof that one strategy is superior, and not evidence of future profitability. Preserve the original REA report and its hash; generate a separate SENEX report and compare only after accounting and fill-policy parity are verified.

## 9. Test and acceptance criteria

The implementation uses test-driven development. Each new externally visible behavior must have a focused test observed failing for the expected reason before its minimal implementation is added.

Required tests include:
- malformed candles, duplicate/out-of-order timestamps, and empty input fail closed;
- market orders fill only at the next candle open with deterministic configured costs;
- limit orders persist, fill only on a later range touch, receive a favorable open-through price, expire only when configured, and remain pending after the final candle;
- cash, inventory, and per-fill caps cannot be exceeded; sell orders cannot create a short position;
- weighted average entry, fees on every fill, realized/unrealized PnL, equity, and drawdown reconcile; net PnL must equal ending equity minus starting cash, entry fees must be allocated consistently between realized and open inventory, and spread/slippage attribution must not be deducted twice;
- identical inputs/configuration produce byte-stable metric payloads and the same report digest;
- Grid/DCA/Combo produce deterministic intents, respect pending-order suppression and caps, and share one ledger contract;
- 2x/3x costs never reduce reported costs relative to the baseline profile;
- no backend import, network call, live-order path, or production runtime wiring is introduced.

Acceptance requires focused tests plus the relevant full SENEX test suite to pass, an offline historical report with source/config hashes, and an explicit statement of limitations. A positive return is not itself a pass criterion; an inconclusive or negative result is an acceptable scientific outcome.

## 10. Delivery sequence and excluded work

1. Implement and test contracts, Decimal ledger, and deterministic replay first.
2. Port Grid, DCA, and Combo as interchangeable strategies and test each independently.
3. Add frozen-config comparison, equal-capital benchmark, cost stress, walk-forward evaluation, and report provenance.
4. Run the canonical local CSV only when its path and SHA-256 are verified; do not download or substitute data automatically.

Excluded: backend/runtime integration, UI/API exposure, BINANCE_SIM changes, model fitting, parameter search, vendor account/API integration, any live/testnet order path, commits, pushes, merges, PRs, workflows, deployment, or paid resources.

## 11. Self-review

- Scope is limited to one isolated research package and its tests; production runtime boundaries are explicit.
- The replay timing prevents same-candle look-ahead and explicitly preserves resting limits.
- Cost accounting is separated by type and stress-tested; exposure comparability is addressed through shared caps and an explicitly labelled benchmark.
- The specification does not promise profit or assert equivalence to any commercial product.
- The existing frozen scientific orders and prospective evidence remain untouched.
