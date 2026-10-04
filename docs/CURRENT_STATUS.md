# SENEX — Current Status

**Evidence snapshot:** 2026-10-04 around 01:15 UTC
**Mode:** PAPER-only
**Scientific state:** EDGE=UNPROVEN

This file is a dated presentation snapshot. Fresh GitHub and H011 evidence supersedes it.

## Authority

| Item | State |
| --- | --- |
| GitHub repository | `simondalmasso/SeneX` |
| Default branch | `main` |
| Main head | `a181dc901d02664345124ad08b4079a7114b0707` |
| Main head change | PortfolioAnalytics fee double-subtraction fix (#100) |
| H011 source commit | `5e074b230450dd288de01f5e5ad0b2f25efd8e7b` |
| H011 provenance | exact=true; build digest matches runtime files |
| Public runtime | H011, GET-only API |
| Open ORDER097 PR | #102, research-only, unmerged |
| ORDER097 head | `291f64ef7827d4473eb92bfa25277e24f8c8b396` |
| ORDER097 exact-head CI | `37165461381` = SUCCESS |
| ORDER097 unresolved review threads | 0 |
| ORDER097 remaining blocker | target-aligned historical 5m resolution corpus not persisted |

The main/runtime SHA difference is known and is not, by itself, a deployment defect. The deployed runtime predates the reporting-only accounting correction in #100.

## H011 safety

Observed directly from `/healthz`, `/readyz`, provenance and OpenAPI:

```text
trade_mode=PAPER
orders_enabled=false
live_capital_locked=true
hard_paper_lock=true
public_non_GET_routes=0
provenance_exact=true
readiness=READY
```

The public API currently exposes 15 GET operations and no POST/PUT/PATCH/DELETE operations.

## BTC scientific authority

Current independent, non-overlapping 1h cohort:

| Bucket | W / N | Win rate | Gate |
| --- | ---: | ---: | --- |
| LONG | 145 / 270 | 53.70% | simple directional gate PASS |
| SHORT | 201 / 418 | 48.09% | FAIL |
| GLOBAL | 346 / 688 | 50.29% | FAIL |

The 95% Wilson lower bound for the global cohort is approximately **0.465634**, below the required 0.50 evidence threshold.

The runtime therefore reports `score_status=REJECTED`. Raw model conviction is explicitly not a validated probability; Brier/ECE authority remains disabled.

## PAPER execution

Current H011 PAPER state:

- hypothetical starting cash: USD 10,000;
- current execution cash: USD 9,979.51;
- current engine epoch: 4 closed positions / 4 orders;
- trade journal: 7 closed trades, 1 win / 6 losses;
- journal net PnL: USD -25.06;
- journal fees: USD 6.88;
- profit factor: 0.182;
- ShadowLive: 4 fills, below the minimum evidence requirement;
- LiveGate: 3/6 diagnostic conditions pass, but the structural PAPER policy keeps it locked.

### Important accounting/presentation caveat

The current execution-engine state reports 4 closed positions while the durable journal contains 7 trades. No explicit `epoch_reset` marker exists in `main`. Therefore presentation-layer comparisons must label engine-epoch metrics and journal-history metrics separately instead of implying one common sample.

## GPTrader treatment

Current public GPTrader state:

```text
paper_only=true
simulation_only=true
live=false
orders_enabled=false
packet_count=1086
take_count=0
abstain_count=16
closed_hypothetical_positions=0
verdict=INSUFFICIENT_DATA
edge=UNPROVEN
```

`last_run_id` remains the 2026-09-28 canary. Packets have continued to accumulate while treatment decisions have not, so the treatment experiment is operationally idle.

## Known presentation/governance debt

1. `AUD_CANON.md` and `ARQ_CANON.md` are ORDER084-era historical state, not current truth.
2. The old DeepSeek runbook contains stale expected test counts and old provider-transition instructions.
3. Production code, dormant/legacy code and testnet capability were not clearly separated in the root documentation.
4. Journal history and current execution-engine epoch are easy to conflate.
5. `LiveGate` thresholds are useful diagnostics, but passing them would not establish economic EDGE or authorize live capital.
6. H011 has not deployed #100; this is known drift and does not authorize an automatic deploy.

## Immediate gates

No current evidence authorizes:

- LIVE;
- real orders;
- capital;
- deployment solely to reconcile SHAs;
- treating raw `up_prob` as a calibrated probability;
- claiming incremental Polymarket edge before ORDER097 obtains target-aligned 5m labels.
