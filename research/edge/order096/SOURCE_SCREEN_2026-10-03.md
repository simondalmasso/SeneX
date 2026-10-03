# ORDER096 — External Source / Strategy Screen (2026-10-03)

STATUS=RESEARCH_ONLY
EDGE=UNPROVEN
NEW_RUNTIME_DEPENDENCIES=0
NEW_PAID_COST=0
PRODUCTION_INTEGRATIONS=0

Purpose: decide whether newly proposed repositories, models, and TradingView tools add information that SENEX does not already have. The default is **do not integrate** unless a source removes a concrete uncertainty or defines a distinct falsifiable strategy.

## Decision matrix

| Source | What it actually adds | Existing SENEX overlap | Decision |
|---|---|---|---|
| NVIDIA/OpenShell | kernel/network/filesystem policy sandbox for autonomous agents; endpoint-scoped credential injection; policy verification | ORDER095 already isolates external collectors behind out-of-process bridges and keeps credentials outside SENEX | **HOLD / reference only**. Valuable if external browser agents later become autonomous or remotely supplied; too much runtime/control-plane surface for current dormant shadow collectors |
| Agent-Reach | discovery/routing layer across X, YouTube, Reddit, GitHub and other sources | already integrated in ORDER095 through a strict external JSON bridge | **ALREADY COVERED**. No second integration |
| awesome-pinescript | curated PineScript links/resources | discovery only; no signal, execution or data contract | **RESEARCH INDEX ONLY** |
| vllm-sr Decision 2.0 | open decision-model family (0.6B→27B), Transformers/custom code, classification/feature-extraction orientation | GPTrader already owns model-based treatment; SENEX EDGE remains unproven | **DO NOT INTEGRATE NOW**. Adds model/inference surface before proving a data/decision gap; custom-code models also enlarge supply-chain/runtime risk |
| Prism Legal OS | contract/legal workflow platform (React/Express/Postgres/Qdrant/MinIO, AGPL) | no relevance to BTC/Polymarket signal, execution or EDGE calibration | **REJECT** |
| HTF Fractal Bars | higher-timeframe OHLC/C1-C2-C3 visualization | SENEX already has a 4h regime; no direct predictive rule in the tool | **NO FEATURE NOW**. Distill only a completed-HTF-state hypothesis if later needed |
| Premium & Discount Map | location within confirmed HTF range; explicitly not a forecasting system | SENEX has no explicit HTF range-location feature | **TESTED AS MINIMAL BASELINE**: prior completed 4h range midpoint reversion; recent screen does not support promotion |
| Tech Leadership Map+ | QQQ/SPY relative leadership and US equity volume internals | no current equity/breadth dependency in SENEX | **REJECT FOR CURRENT BTC EDGE**. Adds cross-asset data and session semantics without evidence of incremental BTC short-horizon value |
| HTF Liquidity Map | prior completed HTF highs/lows and sweep observations | SENEX has orderbook liquidity but not price-level HTF sweep structure | **TESTED AS MINIMAL BASELINE** via prior completed 4h high/low |
| Sweep Reversal Map+ | confirmed swing liquidity sweep → reclaim → structure/displacement confirmation | no equivalent explicit price-level sweep state in SENEX | **KEEP CONCEPT, NOT FULL SCRIPT**. First-stage sweep/reclaim proxy tested; weak recent results mean no production integration |
| Minicharts Pro+ | MTF visualization; no trade signal | dashboard/workflow aid only | **REJECT AS MODEL INPUT** |

## Why OpenShell is not being installed

OpenShell is credible and relevant to agent security, but ORDER095 is currently dormant, shadow-only, and uses strict out-of-process bridge contracts. Adding an OpenShell gateway/sandbox lifecycle now would solve a future autonomy problem, not a demonstrated current failure.

Revisit only if at least one becomes true:
- browser collectors execute untrusted code;
- remote users can supply arbitrary capture targets;
- credentials must be injected dynamically into collectors;
- multiple autonomous collector agents need per-agent filesystem/network policy.

Until then: keep the simpler bridge boundary.

## Why Decision 2.0 is not being installed

The current collection contains several freshly published decision models from ~0.6B to 27B. They are tagged as decision/classification/feature-extraction models and use custom model code.

That does not establish:
- BTC directional information;
- calibrated Polymarket probability estimation;
- incremental value over SENEX/GPTrader;
- lower inference cost;
- deterministic reproducibility under the current PAPER protocol.

Adding a new model before identifying an information deficit would make attribution harder. It remains a possible **later challenger model** only after the sealed T0 evaluation harness can compare it on identical packets.

## PineScript resource policy

`awesome-pinescript` is useful as a catalog, not as a dependency.

For any Pine-derived idea:
1. identify the mathematical information family;
2. check whether SENEX already has that family;
3. implement the smallest independent causal baseline;
4. freeze defaults before outcomes;
5. compare on identical timestamps;
6. kill the idea if it does not add incremental OOS information.

No copy/paste of third-party Pine code into SENEX is required for this research path.

## Current integration disposition

```text
INTEGRATE_OPEN_SHELL=NO
INTEGRATE_AGENT_REACH_SECOND_TIME=NO
INTEGRATE_AWESOME_PINESCRIPT=NO
INTEGRATE_DECISION_2_MODELS=NO
INTEGRATE_PRISM_LEGAL_OS=NO

TEST_HTF_RANGE_LOCATION=YES_RESEARCH_ONLY
TEST_HTF_SWEEP_RECLAIM=YES_RESEARCH_ONLY
TECH_LEADERSHIP=NO_CURRENT_MISSION
MINICHARTS=NO_SIGNAL
PRODUCTION_FEATURE_CHANGE=NO
```
