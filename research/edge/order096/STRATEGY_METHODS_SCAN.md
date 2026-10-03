# ORDER096 — Trading Strategy / Method Scan

Purpose: harvest **experience and testable hypotheses**, not import trading stacks.

## Sources inspected

- `alsk1992/CloddsBot` — crypto prediction-market HFT strategies and execution conventions.
- `HarrierOnChain/Polymarket` — strategy catalog and latency-oriented prediction-market execution.
- `evan-kolberg/prediction-market-backtesting` — queue/latency/L2 execution realism.
- `SII-WANGZJ/Polymarket_data` — 107GB / 1.1B-record Polymarket historical dataset with maker/taker user trajectories.
- `ent0n29/polybot` — strategy replication / trader-behavior research stack.
- `lihanyu81/polymarket_lp_tool` — liquidity/reward-oriented tooling.
- broad prediction-market tool catalogs — used only for discovery.

## KEEP: 3 hypotheses/methods that may add information

### H096-S1 — Spot→prediction-market lag / stale repricing

CloddsBot's crypto momentum strategy explicitly asks whether spot moved while the Polymarket binary token has not yet repriced sufficiently.

Why this is interesting for SENEX:
- SENEX already computes spot/derivative directional evidence.
- SENEX already observes Polymarket context/orderbooks.
- What is missing is a **paired cross-market lag variable**: how much the binary price moved relative to a contemporaneous spot move and time-to-expiry.
- This is not another TA indicator. It is a market-efficiency hypothesis.

Do not copy Clodds' rough `0.50 + move * 5` fair-value heuristic. Calibrate the relationship from historical/paper T0 observations.

Required test:
- same T0 timestamp;
- spot move over frozen windows (e.g. 10s/30s/60s);
- Polymarket UP/DOWN mid and age;
- time to expiry;
- spread/depth;
- prospective change/resolution;
- test whether residual lag predicts subsequent token repricing or settlement after costs.

This is the highest-information new strategy candidate from the scanned repos.

### H096-S2 — Oscillation / maker-only microstructure (Penny-Clipper family)

Clodds' Penny Clipper does not claim directional forecasting from indicators. It looks for:
- a bounded token-price zone;
- repeated short-window reversals;
- price below its local mean;
- narrow spread;
- spot confirmation;
- maker execution.

Why it may matter:
- it is an **execution/microstructure** hypothesis, separate from SENEX directional EDGE;
- it may monetize oscillation even when 1h direction is uninformative.

Why it is not immediately added:
- edge may be entirely maker-fill/queue-position dependent;
- SENEX already has queue/book-walk execution modeling, but not this specific oscillation strategy;
- it needs L2 replay and realistic passive fill evidence.

Disposition: paper/backtest research only, after H096-S1.

### H096-S3 — Empirical execution calibration from replay/logs

The strongest transferable lesson from `prediction-market-backtesting` and Clodds is methodological:
- replay L2;
- model queue position;
- explicit quote age and latency;
- calibrate latency/fee/fill penalties from observed logs;
- never infer maker fills from touch alone.

SENEX already has latency/slippage/queue models, so **do not import another engine**.

Action: calibrate the existing SENEX execution-fidelity parameters against collected paper observations rather than retaining generic 50–300ms / fixed slippage assumptions forever.

This improves ECONOMIC_EDGE calibration without adding a new predictive feature.

### Concrete execution gaps isolated by the scan

Repository comparison narrows the useful transfer to two missing maker-economics/safety terms plus one new cross-market construct, not another execution stack:

- **cross-market lag residual** — this is the genuinely missing research variable. SENEX already persists Polymarket `freshness_s` and `seconds_to_close` at decision time, so quote age/time-to-close should be reused rather than reimplemented. The experiment still needs a synchronized residual joining those decision-time market fields to frozen spot-move windows. If sub-snapshot timing proves material, add explicit per-side/event observation timestamps as evidence rather than pretending `freshness_s` is exact exchange event time.
- **maker rebate as a separate economic term** — the external L2 replay work models venue-documented maker rebates separately from taker fees. SENEX has fee/slippage/impact machinery but no canonical `maker_rebate` term was found. Any maker-strategy research must report gross edge, fees, rebate, and net edge separately and fail closed when the rebate schedule is not evidenced for that market.
- **anti-sniping / midpoint-jump state** — the LP tooling uses midpoint-jump filtering, stability confirmation, fill cooldown, and max chase limits. These are execution-safety controls, not predictive alpha. They are relevant only if a maker hypothesis survives replay.

What is **not** missing:
- Polymarket decision-time freshness and time-to-close already exist as `freshness_s` and `seconds_to_close`;
- queue-position/fill-probability modeling already exists in `backend/portfolio/execution_fidelity.py`;
- latency and slippage fields/models already exist;
- orderbook depth/walk logic already exists.

Therefore ORDER096 must calibrate or add evidence fields around the existing execution model rather than importing another engine.

## HOLD / MAYBE

### Whale/copy-trading signals

Harrier and Polybot emphasize wallet replication, whale tracking and user-behavior scoring. The SII dataset provides maker/taker user histories large enough for research.

Potentially useful as an external evidence family:
- wallet skill persistence;
- fresh-wallet behavior;
- lead/lag of high-skill wallets.

But this is a separate hypothesis and risks severe selection/survivorship bias. Do not add until the current signal/market-lag experiments are exhausted.

### Complete-set / cross-market arbitrage

Polybot and Harrier both include non-directional arbitrage.

This may be economically useful, but it **does not calibrate SENEX directional EDGE**. It belongs to a separate product/strategy order if the owner wants a prediction-market arbitrage desk.

## REJECT NOW: already have / wrong mission / overengineering

- Orderbook imbalance as a new feature — SENEX already has it.
- Generic latency/slippage model — SENEX already has it; calibrate existing model instead.
- Generic risk engine / Kelly / VaR / circuit breaker — already represented in SENEX research/portfolio stack.
- Market making / spread farming — operationally large surface, not needed to calibrate current EDGE.
- DCA — portfolio accumulation tactic, not evidence of predictive edge.
- Weather strategy — unrelated to BTC/ETH canonical signal.
- Pydantic agent builders / Claude terminals / MCP wrappers — infrastructure duplication.
- 150-tool catalogs — discovery only.
- 107GB dataset ingestion into production — unnecessary. Query/sample offline only if a specific hypothesis requires it.
- LuxAlgo paid/opaque indicators — no reproducible canonical feature contract.

## Priority order by information value / complexity

1. **Spot→Polymarket lag residual** — high novelty, uses data SENEX largely already observes.
2. **Paired SENEX-vs-indicator disagreement test** — cheap, directly answers whether ORDER096 indicators add anything.
3. **Empirical execution calibration** — no new strategy engine; improves economic truth.
4. **Penny-clipper microstructure replay** — distinct but fill-model dependent.
5. Wallet/whale skill persistence — later.
6. Arbitrage/market making desk — separate mission, not EDGE calibration.

This list is intentionally short.
