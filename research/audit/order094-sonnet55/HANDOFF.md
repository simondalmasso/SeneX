# ORDER094-AUD — SONNET 5.5 ADVERSARIAL FULL-SYSTEM AUDIT + COSMIC EDGE CALIBRATION PROGRAM

PROJECT=SENEX
ROLE=INDEPENDENT_ADVERSARIAL_AUDITOR
TARGET_MODEL=SONNET_5_5
MODE=READ_MANY_WRITE_BRANCH_ONLY
BRANCH=audit/order094-sonnet55-cosmic-edge
BASE_MAIN_SHA=5e074b230450dd288de01f5e5ad0b2f25efd8e7b
MAIN_MUTATION=FORBIDDEN
H011_MUTATION=FORBIDDEN
DEPLOYMENTS=0
LIVE=NO
REAL_ORDERS=0
CAPITAL=0
PAPER_ONLY=true
EDGE=UNPROVEN

## Mission

Audit SENEX end-to-end as if the working assumption were that the claimed edge is false, the experiment design is biased, and every attractive backtest can be explained by leakage, selection, costs, regime luck, stale data, or accounting artifacts.

Then produce a concrete, implementation-ready **COSMIC EDGE CALIBRATION PROGRAM** that can move SENEX from research accumulation to a falsifiable, continuously calibrated, operational PAPER research system.

Do not merely review code quality. Answer the harder question:

> What exact evidence, calibration architecture, market baselines, prospective protocol, and promotion gates would make an economically meaningful SENEX EDGE claim difficult to fake and easy to falsify?

The output must be useful even if the correct conclusion is that SENEX currently has no edge.

## Branch/write discipline

You may write ONLY under:

`research/audit/order094-sonnet55/`

on branch:

`audit/order094-sonnet55-cosmic-edge`

Do not modify product code, tests, workflows, deployment files, secrets, H011, Northflank, D1, GPTrader runtime, GPTrader task, ORDER093 ledger, or main.

Do not merge your own branch.

Persist checkpoints frequently. If context/quota ends, leave the branch resumable rather than summarizing from memory.

## Authority hierarchy

Reconstruct current truth before analysis.

1. GitHub current repository + current main are CODE/CI authority.
2. Current H011 runtime readback is runtime truth.
3. GitHub issues/orders are governance/evidence, but stale issue prose does not override live readback.
4. AUD_CANON.md and ARQ_CANON.md are historical/canonical context, not permission to assume their LAST_VERIFIED state is still current.
5. External market-data providers are independent evidence sources, never SENEX authority.

Record every important fact with source, timestamp, and confidence.

## Current bootstrap facts to REVERIFY, not assume

At handoff creation, the latest observed repository main commit was:

`5e074b230450dd288de01f5e5ad0b2f25efd8e7b` — ORDER092C direct HTTP GPTrader client.

The current GPTrader zero-cost prospective lane is governed separately by:

- ORDER093-AUD issue #92
- direct-treatment ledger issue #91

Do not alter either. Treat them as ongoing experimental evidence only.

Recent H011 observations showed PAPER locks and an exact provenance object, but you must independently re-read current H011 before relying on them.

## Required tools / evidence palette

Use the tools below when they materially improve the audit. Do not use a tool merely because it exists.

### Repository / engineering / adversarial workflow

- Superpowers
- Skillquiver
- get-fable
- InsForge
- EW_knowledge_forge
- QA Wolf, if available
- GitHub
- H011 public read-only runtime

Use these for structured investigation, threat modeling, reproducibility, test-gap analysis, architecture reconstruction, and adversarial review.

### Web / independent research

- Exa
- Parallel Search

Use these to verify current market microstructure assumptions, exchange fee schedules, methodology literature, regime events, provider limitations, and external benchmark definitions.

### Mathematical / statistical reasoning

- Wolfram
- Data tooling

Use these for power/sample-size reasoning, confidence intervals, sequential-testing boundaries, calibration mathematics, bootstrap/Monte Carlo checks, and sanity calculations.

### Exchange / crypto market evidence

- Binance
- Bybit
- Kraken
- CoinGecko
- CoinMarketCap
- Exum / ExoScope Crypto
- CryptoAudit
- TraderSpy
- TradingCursor
- LONA Trading Assistant

These are **independent comparison and falsification sources**.

They must not be treated as ground truth without timestamp/provenance comparison. Explicitly document differences in symbol, venue, timestamp, bar construction, spread, fee tier, funding, mark/index/last price semantics, and latency.

### Broader financial / market intelligence

- Alpaca
- Alpha Vantage
- TickerLayer
- FMP
- The Fly Market Intelligence
- Next Stock - Market Insights
- Funnel
- Dataslayer

Use only if they contribute a real benchmark, regime/control variable, macro/event annotation, market-prior estimate, or independent data-quality cross-check.

Do not pollute the core crypto EDGE claim with unrelated equity/marketing data just to increase feature count.

## Mandatory adversarial posture

For every apparent positive result, attempt to explain it through at least these nulls:

1. lookahead/future leakage;
2. timestamp mismatch;
3. survivorship/selection bias;
4. multiple testing / researcher degrees of freedom;
5. regime luck;
6. venue-specific artifact;
7. stale-entry assumption;
8. spread/fee/slippage under-modeling;
9. execution fill impossibility;
10. price-source mismatch;
11. duplicated/correlated observations;
12. overlapping-horizon dependence;
13. class imbalance / trivial directional prior;
14. GPTrader selection effect;
15. confidence score monotonicity without calibration;
16. leakage through dashboard/outcome fields;
17. restart/persistence/accounting discontinuity;
18. denominator drift / sample definition change;
19. post-hoc threshold choice;
20. hidden external-data dependence.

A finding is not strong until the obvious null explanations are actively attacked.

## EDGE ontology — do not collapse these

Treat EDGE as a vector, not one number.

At minimum distinguish:

- **Signal edge** — SENEX score/direction contains predictive information at T0.
- **Calibration edge** — score magnitude maps reproducibly to conditional outcome probability/return.
- **Selection edge** — abstaining/choosing subsets improves economics without post-hoc cherry-picking.
- **Execution edge** — the signal survives realistic fees, spread, slippage, latency and fill constraints.
- **Portfolio edge** — sizing/risk aggregation adds value beyond signal alone.
- **Regime edge** — effect persists or is explainably conditional across market states.
- **Cross-venue edge** — not an artifact of one exchange feed.
- **Incremental edge** — adds value beyond simple market priors and deterministic baselines.
- **Operational edge** — survives restarts, stale inputs, partial outages and real scheduling.
- **Economic edge** — positive expected net value with uncertainty bounds, not just hit rate.

A single positive PnL curve cannot satisfy all ten.

## Required full-system audit

Reconstruct and audit:

1. prediction generation path;
2. score semantics (`confidence`, `ev`, `up_prob`, direction);
3. feature lineage and decision-time availability;
4. persistence and timestamps;
5. settlement/outcome authority;
6. native PAPER control;
7. GPTrader treatment;
8. sealed/direct T0 data boundaries;
9. current public observability;
10. restart/durability behavior;
11. execution assumptions;
12. fee/slippage/fill model;
13. scientific cohort definitions;
14. prior prospective experiments, including failures/inconclusive runs;
15. current unresolved governance/science gaps;
16. external-data dependency and venue consistency;
17. CI/tests that actually protect scientific truth versus tests that only protect software behavior.

Produce an explicit `EVIDENCE_TRUTH_TABLE.md` with columns:

`CLAIM | CURRENT_EVIDENCE | SOURCE | TIME_SCOPE | POPULATION | FAILURE_MODE | CONFIDENCE | WHAT_WOULD_FALSIFY`

## COSMIC EDGE CALIBRATION PROGRAM

The proposal must be ambitious but implementable.

Design at least the following layers.

### Layer A — immutable T0 event fabric

Define one canonical decision-time event schema.

Requirements:

- immutable T0 packet;
- source timestamps and receipt timestamps;
- venue and symbol normalization;
- raw score semantics preserved;
- no outcomes/future fields;
- cryptographic identity/hash;
- causal ordering;
- explicit freshness;
- replayable provenance;
- duplicate and overlap identity.

Explain whether existing sealed packets/direct prediction snapshots are sufficient or what minimum additive fields are missing.

### Layer B — truth/settlement fabric

Define outcome truth separately from T0.

Require:

- exact horizon definitions;
- source-consistent settlement;
- independent timestamp tolerance;
- missing-price policy;
- no mutation of T0;
- multiple horizons where scientifically useful;
- explicit stale/unsettled state;
- reproducible settlement proof.

### Layer C — calibration engine

Propose a preregistered calibration stack suitable for an uncalibrated score.

Evaluate, not blindly adopt:

- rank/monotonic diagnostics;
- reliability bins with uncertainty;
- isotonic calibration;
- Platt/logistic calibration;
- beta calibration;
- hierarchical/shrinkage calibration by symbol/regime;
- rolling/expanding calibration windows;
- conformal or distribution-free uncertainty where appropriate.

Do not use Brier/log-loss as if raw SENEX scores were probabilities until a probability mapping is explicitly defined.

Every calibrator must be trained only on historical resolved data and evaluated prospectively/out-of-sample.

### Layer D — market-prior and dumb-baseline lattice

SENEX must beat meaningful baselines.

At minimum include:

- ALWAYS_ABSTAIN;
- FOLLOW_ALL_DIRECTIONAL;
- fixed predeclared confidence threshold;
- EV-sign rule when semantically valid;
- naive directional base-rate prior;
- previous-return / simple momentum prior;
- simple mean-reversion prior;
- venue market-implied or mechanically available prior if truly available at T0;
- GPTrader treatment;
- native SENEX PAPER control.

Do not tune the baselines after seeing the test cohort.

### Layer E — execution realism ladder

Define increasingly hostile execution models:

E0 = frictionless diagnostic only.  
E1 = taker fee + contemporaneous spread.  
E2 = fee + spread + empirical slippage.  
E3 = latency-adjusted executable price.  
E4 = queue/fill/capacity constraints where data supports them.  
E5 = cross-venue stress / outage / volatility shock.

EDGE promotion must depend on surviving an explicitly chosen realistic rung, not E0.

### Layer F — regime atlas

Propose a small, stable, preregistered regime taxonomy.

Candidates may include:

- volatility quantiles;
- trend vs chop;
- liquidity/spread state;
- funding/basis state;
- session/time-of-day;
- macro/news shock windows;
- exchange dislocation;
- risk-on/risk-off proxy.

Keep dimensionality low enough to avoid slicing until something looks good.

Use external providers only to annotate regimes with timestamps available at or before T0.

### Layer G — multi-venue triangulation

Use Binance, Bybit, Kraken and other credible sources to test whether the observed relation is:

- source-specific;
- exchange-specific;
- timestamp-specific;
- robust to mark/index/last-price definitions.

Do not average incompatible prices blindly.

### Layer H — prospective experiment factory

Design a repeatable prospective protocol:

- pre-register hypothesis;
- freeze code/thresholds;
- freeze inclusion/exclusion rules;
- freeze horizon;
- freeze execution model;
- freeze stopping rule;
- hash manifest;
- collect prospectively;
- forbid early peeking;
- settle only after maturity;
- produce immutable result artifact;
- independent reanalysis.

No backfill of missed T0 decisions.

### Layer I — sequential evidence / sample policy

Propose explicit sample and stopping rules.

Use Wolfram or equivalent quantitative tooling to reason about:

- minimum detectable effect;
- effective N under overlapping 15m/1h horizons;
- autocorrelation/design effect;
- confidence/credible intervals;
- sequential testing inflation;
- bootstrap/block-bootstrap;
- false-discovery control across symbols/horizons/regimes;
- minimum calendar span.

Do not declare a universal magic N. Tie N to effect size and dependence.

### Layer J — EDGE scorecard

Design a machine-readable scorecard that can say:

- `SIGNAL_EDGE_NOT_ESTABLISHED`
- `CALIBRATION_INVALID`
- `SELECTION_EDGE_ONLY`
- `EXECUTION_ASSUMPTIONS_DOMINATE`
- `REGIME_CONDITIONAL_ONLY`
- `VENUE_ARTIFACT_SUSPECTED`
- `INCREMENTAL_EDGE_NOT_ESTABLISHED`
- `PROSPECTIVE_ECONOMIC_EDGE_EVIDENCE`
- `INSUFFICIENT_DATA`

Avoid a single vanity score.

For each state specify exact evidence and promotion/demotion criteria.

## Required “cosmic” proposal

Produce one architecture called:

`COSMIC_EDGE_CALIBRATION_V1`

It must answer:

- What data is captured at T0?
- What is immutable?
- What is allowed only post-T0?
- What is calibrated?
- What is never calibrated?
- What baselines are frozen?
- What external data is used, and why?
- What execution model is authoritative?
- How are regimes defined without post-hoc slicing?
- How do GPTrader and native SENEX form treatment/control?
- How are overlapping samples handled?
- What is the prospective cadence?
- What exact conditions promote a hypothesis?
- What exact conditions kill it?
- What must be true before any future LIVE discussion is even allowed?

The design should minimize moving parts. “Cosmic” means intellectually complete, not maximal feature count.

## Promotion ladder

Design a conservative ladder such as:

`UNPROVEN -> SIGNAL_EVIDENCE -> CALIBRATED_SIGNAL_EVIDENCE -> PAPER_ECONOMIC_EVIDENCE -> REPLICATED_PAPER_EVIDENCE -> LIVE_DISCUSSION_ELIGIBLE`

You may change the names, but each transition must have measurable gates.

This order does **not** authorize any transition to LIVE.

## Required attack on GPTrader

Treat GPTrader as a treatment, not a savior.

Audit whether GPTrader:

- adds information or merely thresholds SENEX;
- benefits only from abstention;
- exploits future leakage;
- changes sample composition;
- increases/decreases turnover;
- improves net results after execution costs;
- introduces model drift/non-reproducibility;
- can be replaced by a deterministic rule.

Require paired comparison against deterministic policies on the same eligible packet set.

## Required external-data table

Create `EXTERNAL_DATA_MATRIX.md`.

For every used provider record:

`PROVIDER | DATA_USED | VENUE/SYMBOL | TIME_SEMANTICS | LATENCY | COST | LIMITS | ROLE | T0_ALLOWED? | RISKS`

A provider not used should not be listed as evidence merely because it was available.

## Required red-team conclusions

Write `KILL_SHOTS.md` with the ten strongest ways the current EDGE story could be false.

For each:

- likelihood;
- impact;
- current evidence;
- fastest falsification test;
- expected cost;
- whether it blocks calibration now.

At least three kill-shots must be capable of ending a line of research instead of generating another experiment.

## Required implementation roadmap

Write `COSMIC_EDGE_CALIBRATION_V1.md` and a separate `ROADMAP.md`.

Roadmap must be ordered by information value per unit cost.

Each work item must include:

- objective;
- exact artifact/code area likely affected;
- prerequisite;
- test/evidence;
- estimated complexity;
- new infra cost;
- D1 impact;
- contamination risk;
- rollback;
- owner gate needed;
- what uncertainty it removes.

Prefer zero-cost reuse of existing H011/local artifacts before new infrastructure.

## Required final recommendation shape

Do not return vague “improve data quality / add ML / test more”.

Return:

1. **Current scientific verdict** — strictly evidence-bound.
2. **Top 5 structural blockers to EDGE calibration**.
3. **Top 5 highest-information experiments**.
4. **COSMIC_EDGE_CALIBRATION_V1 architecture**.
5. **90-day PAPER research program** with explicit weekly gates.
6. **Minimum viable instrumentation delta**.
7. **What to delete/stop doing** because it creates research theater rather than information.
8. **Exact next implementation order** for ARQ, but do not implement it.
9. **Explicit kill criteria** under which SENEX should conclude a hypothesis is not useful.
10. **Future LIVE discussion prerequisites**, without authorizing LIVE.

## Forbidden shortcuts

- No “positive PnL = edge”.
- No probability claims from raw confidence/up_prob without calibration.
- No cherry-picked symbol/regime/window.
- No using outcome/current-price tools at decision time.
- No early peeking at frozen prospective cohorts.
- No threshold optimization on the evaluation set.
- No treating correlated overlapping predictions as independent.
- No ignoring fees/spread/slippage.
- No cross-venue price substitution without an explicit mapping.
- No expanding scope to dozens of features because existing evidence is weak.
- No production mutation.
- No secret retrieval.
- No paid resource without owner authorization.

## Mandatory branch artifacts

Create/update all of these:

- `CHECKPOINT.md`
- `SYSTEM_MAP.md`
- `AUDIT_FINDINGS.md`
- `EVIDENCE_TRUTH_TABLE.md`
- `EXTERNAL_DATA_MATRIX.md`
- `KILL_SHOTS.md`
- `COSMIC_EDGE_CALIBRATION_V1.md`
- `ROADMAP.md`
- `FINAL_RECOMMENDATION.md`

All under `research/audit/order094-sonnet55/`.

## Checkpoint contract

After each substantial phase, update CHECKPOINT.md with:

```text
NUM_ORDER=ORDER094-AUD
STATUS=IN_PROGRESS|BLOCKED|AUDIT_COMPLETE
BRANCH=audit/order094-sonnet55-cosmic-edge
BASE_MAIN_SHA=5e074b230450dd288de01f5e5ad0b2f25efd8e7b
OBSERVED_MAIN_SHA=
OBSERVED_H011_SOURCE_COMMIT=
PHASES_COMPLETE=
CURRENT_PHASE=
SOURCES_USED=
FILES_WRITTEN=
CRITICAL_FINDINGS=
EDGE_STATE=
TOP_UNCERTAINTIES=
NEXT_EXACT_ACTION=
MAIN_MUTATIONS=0
H011_MUTATIONS=0
DEPLOYMENTS=0
LIVE=NO
REAL_ORDERS=0
CAPITAL=0
```

## Completion gate

`AUDIT_COMPLETE` only when another engineer could take ROADMAP.md and implement the first calibration phase without asking what the experiment actually means.

The final answer must be adversarial enough that a null result is considered a successful scientific outcome.

STOP after branch artifacts are complete. Do not merge, deploy, or implement.
