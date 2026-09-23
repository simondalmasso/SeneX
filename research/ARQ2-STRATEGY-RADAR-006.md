# ARQ2-STRATEGY-RADAR-006

Status: **COMPLETE**  
Scope: **Amendment B only**  
Base head: `ae07cac5a7c9cde2b8c31ed9b70b3f39c059a52c`

## PolyDekos bounded feed

Reviewed 13 recent concrete posts with resolvable status IDs. PolyDekos is useful as a **discovery feed**, not an evidence authority. The sample is dominated by successful weather traders and large winning examples, creating severe survivorship/selection bias.

Primary Polymarket evidence independently confirms several referenced traders and weather-market behaviors: ShyGuy1, Hans323, the 0x496f… wallet, OnlyLuckNoBrain, shanze8 through the official leaderboard, and donothackmeee through the official weather leaderboard. In those cases the broad behavior often matches the post (many weather markets, low-cent entries, repeated city-temperature exposure), while headline total-PnL and causal claims are not consistently reproducible from current snapshots.

Posts with tiny sample sizes, unresolved identities, or only secondary PnL claims remain `UNVERIFIED_CLAIM`. No weather/event result is pooled with BTC 1h evidence and no wallet-copy signal is created.

## Agent embeddability

### MARKET_JUDGE_SHADOW — reject as duplicate
`jev-trader` demonstrates the reusable pattern clearly: structured market state → typed buy/sell probabilities. Its Monad/Kuru loop, 300ms block budget and order-placement stack are incompatible with SENEX. More importantly, SENEX already has the frozen HYP-008 Jev shadow judgment role. A second judge would duplicate it.

### STRATEGY_CURATOR — sole survivor
`jev-curate` provides the cleanest whole-role match: verbatim inputs, typed filtering/scoring, no vector DB, and an offline/mock test path. SENEX currently has no component that turns external strategy leads into `KEEP | REJECT | NEEDS_EVIDENCE` while preserving primary-evidence provenance.

The role stays strictly outside trading. Its causal shadow metric is research quality: KEEP precision, false-KEEP rate, NEEDS_EVIDENCE recall, and review-time reduction against blinded AUD labels.

### RISK_VETO — reject as duplicate
Prism's strongest design principle is fail-closed deterministic risk gating around a richer observe/reason/simulate loop. SENEX already assigns this responsibility to PortfolioEngine/RiskKernel/ExecutionEngine. Importing Prism's Solana/Meteora, Bun, sqlite-vec memory or pool mechanics would add infrastructure but not a new role.

### ARQ_EVIDENCE_JUDGE — useful pattern, reject as SENEX subsystem
Canny demonstrates a strong governance pattern: facts can block; model judgments advise. That is relevant to the engineering process, but existing AUD/ARQ/CI gates already own completion evidence. It should not be inserted into the market runtime.

### typed-scoring pattern — reuse concept only
KillMyIdea shows one-call multi-question typed scoring plus a deterministic clarity gate. Its startup rubric and KILL/FIX/SHIP thresholds are domain-specific. Treat it as design evidence for the curator schema, not a separate runtime role.

Five additional Jev repositories confirm that compact state → typed decision is general across desktop control, game control, drone tactics and graph navigation. They do not create additional SENEX needs.

## Provider/resource decision

A durable SENEX artifact proves one historical authorized Jev-1.13.0 probe on 2026-09-20. Current runtime/key availability is not live-verifiable in this order, so `JEV_RUNTIME_AVAILABLE=UNRESOLVED`.

Laya remains incompatible with the current H011 process: approximately 1.7GB weights / ~2GB runtime versus the small Python H011 footprint. Any future Laya use must be a separate research worker/sidecar with explicit resources.

Therefore the surviving role is defined with a **provider-neutral typed contract** and `MODEL_PROVIDER=NONE` in this order. No heuristic is substituted for an unavailable provider.

## Decision

`EMBED_CANDIDATE=STRATEGY_CURATOR`

It is the only audited role that:
- does not duplicate current SENEX deterministic ownership;
- has a measurable marginal research value;
- can fail closed to `NEEDS_EVIDENCE`;
- requires no score/gate/order mutation to shadow-test;
- needs no vector DB or agent-to-agent chatter.

Implementation is **not** recommended yet because current model-provider availability/resources were not proven. A later AUD order may authorize one shadow-only implementation after provider check.

ORDER-005 TOP3 remain unchanged. No implementation, CORE/model/score/gate mutation, D1 write, deploy, LIVE action, order, or capital action occurred.

EDGE remains **UNPROVEN**.
