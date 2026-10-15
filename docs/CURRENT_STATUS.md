# SENEX — Current Status

**Evidence snapshot:** 2026-10-04 around 04:15 UTC; deployment addendum verified around 22:45 UTC
**Mode:** PAPER-only
**Scientific state:** EDGE=UNPROVEN

This file is a dated presentation snapshot. Fresh GitHub and H011 evidence supersedes it.

> **POST-AUDIT / PHASE-SHIFT ADDENDUM — 2026-10-05:** Architecture is frozen for scientific work. ORDER100 remains `COLLECTING_PROSPECTIVE_DATA`; no interim prospective loss/edge metric is authorized before its >=300 fresh resolved-market gate. Historical ORDER099 calibration diagnostics are now reproducible from the exact frozen artifacts and identify material absolute calibration error; they do not justify adding feature families, and incremental SENEX edge remains unproven. PR #142 hardened future narrow-feature optimizer convergence without changing ORDER099 or ORDER100 frozen results.
>
> **DATA AUTHORITY:** External Supabase is not a current SENEX operational authority. Current authority is Cloudflare D1 through the SENEX gateway/authority path plus H011 live readback. Legacy compatibility identifiers such as `supabase_client` and `SUPABASE_*` remain in code and deployment contracts and must not be mistaken for an active Supabase source.
>
> **GPTRADER RETIREMENT — CANDIDATE 2026-10-15:** The GPTrader decision service, MCP sidecar, hypothetical trading book, public dashboard panels, external decision client and NVIDIA/DeepSeek provider profile are removed from this candidate. The oracle's durable T0 sealing and optional HTTPS replication are preserved under `backend/t0_audit/`. Legacy packet IDs, schema version and disk path remain solely for persisted-evidence compatibility. This candidate is not deployed until CI and post-deploy provenance/readiness are verified.


> **POST-DEPLOY ADDENDUM — 2026-10-04 ~22:45 UTC:** H011 was deliberately deployed to canonical `main` at `96c41f123175f597788911ab7f52a18ed77c7cdb`. Fresh readback reports exact provenance, READY, PAPER, orders disabled, live capital locked, hard PAPER lock engaged, 17 GET-only public operations, and the isolated BINANCE_SIM lane initialized at `18.63631644 USDT`. No real Binance balance or live-order capability is present.

## Authority

| Item | State |
| --- | --- |
| GitHub repository | `simondalmasso/SeneX` |
| Default branch | `main` |
| Main state | ORDER097/098/099/100 research, isolated BINANCE_SIM PAPER, REA Binance public-data PAPER diagnostics; decision-model retirement is a candidate |
| Historical research verdict | EVALUATED: INCREMENTAL_EDGE_NOT_DEMONSTRATED |
| Active follow-up | ORDER100 = COLLECTING_PROSPECTIVE_DATA |
| H011 last verified pre-candidate source commit | `b8a999eae7d7d958a2658cf37531f40d394f8f5a` |
| H011 provenance | exact=true; build digest matches runtime files |
| Public runtime | H011, GET-only API |
| ORDER097 | merged; target-aligned nested market-only vs market+SENEX harness |
| ORDER098 | merged; GET-only T0 export + fail-closed Polymarket 5m resolution corpus |
| ORDER099 | merged and evaluated; historical null verdict |
| ORDER100 | merged; frozen prospective low-disagreement confirmation |
| ORDER126 provider profile | retired/removed from this candidate; historical artifacts retained; no provider call enabled |
| ORDER098 corpus | 3,568/3,568 exact markets accepted; 0 rejected |
| ORDER099 evidence floor | at least 200 unique resolved markets before any edge verdict |

H011 was last verified at `b8a999eae7d7d958a2658cf37531f40d394f8f5a` before the retirement candidate. This branch must pass canonical CI, be reviewed and explicitly deployed, and then pass fresh provenance/readiness checks. A branch or green CI alone is not a runtime change and does not authorize LIVE, orders, capital, paid provider calls or auto-remediation.

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

The last verified deployed H011 runtime (source commit `b8a999eae7d7d958a2658cf37531f40d394f8f5a`, before the GPTrader retirement deploy) exposes 19 GET operations, including three legacy GPTrader routes, and no POST/PUT/PATCH/DELETE operations. The merged retirement commit removes those three routes; expected post-deploy count is 16 GET operations. This expectation must be verified against the actual runtime after deployment.

## BINANCE_SIM isolated PAPER lane

Fresh H011 readback after deployment:

```text
lane_id=BINANCE_SIM_18_63631644
venue=BINANCE_SIMULATED
account_label=SIMULATED / PAPER — NOT BINANCE BALANCE
starting_bankroll_usdt=18.63631644
cash_usdt=18.63631644
equity_usdt=18.63631644
simulation_only=true
live_orders_possible=false
hard_paper_lock=true
open_position=null
closed_trade_count=0
total_simulated_orders=0
EDGE=UNPROVEN
research_verdict=INCREMENTAL_EDGE_NOT_DEMONSTRATED
```

The lane has its own isolated ledger and follows SENEX BTC signals only after deployment. A null open position and zero trades are valid until a later directional SENEX signal occurs. PAPER PnL from this lane is operational simulation evidence only; it is not scientific proof of edge.

## Historical ORDER126 provider preparation — retired 2026-10-15

The former candidate used the NVIDIA NIM / DeepSeek V4.1 Flash profile as an offline-only probe. The active profile implementation, runbook and dedicated tests are removed from this candidate; this historical section records why no provider network call was ever enabled.

```text
provider=NVIDIA_NIM / deepseek-ai/deepseek-v4.1-flash
status=PREPARED / OFFLINE-ONLY
zero_cost_owner_lock=true
real_provider_network=DISABLED_BY_CODE
NVIDIA_API_KEY alone cannot enable network
MCP submit during provider-specific probe=false
LIVE=false
capital=0
EDGE=UNPROVEN
```

No external decision-model client remains in the candidate. No paid inference, API credential or network-call path is introduced by the OpenSRE observer.

## BTC scientific authority

Current independent, non-overlapping 1h cohort:

| Bucket | W / N | Win rate | Gate |
| --- | ---: | ---: | --- |
| LONG | 145 / 270 | 53.70% | operational point-estimate gate PASS; not edge evidence |
| SHORT | 201 / 418 | 48.09% | operational point-estimate gate FAIL |
| GLOBAL | 346 / 688 | 50.29% | operational point-estimate gate FAIL |

The 95% Wilson lower bound for the global cohort is approximately **0.465634**, below the required 0.50 evidence threshold.

Directional `long_1h` / `short_1h` / `global_1h` `pass` fields are operational point-estimate thresholds used by PAPER control logic. They are not uncertainty-adjusted and are not statistical EDGE evidence. The uncertainty-aware evidence gate is the separate 95% Wilson lower-bound check under `quality.gates.wilson_lower_95`.

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

## Retired GPTrader treatment — historical evidence only

The prior local canary reported 0 TAKE decisions, no closed hypothetical positions and `INSUFFICIENT_DATA`. This was not productive trading evidence. The decision service and its public surfaces are removed from the current candidate. The durable T0 receipt format is preserved separately to avoid breaking prediction lineage and historical evidence.

## Known presentation/governance debt

1. `AUD_CANON.md` and `ARQ_CANON.md` are ORDER084-era historical state, not current truth.
2. The prior DeepSeek/GPTrader provider runbook and active provider implementation are retired; only bounded historical incident/evidence files remain.
3. Production code, dormant/legacy code and testnet capability were not clearly separated in the root documentation.
4. Journal history and current execution-engine epoch are easy to conflate.
5. `LiveGate` thresholds are useful diagnostics, but passing them would not establish economic EDGE or authorize live capital.
6. The last verified H011 runtime before this retirement candidate was `b8a999eae7d7d958a2658cf37531f40d394f8f5a`. Deploy the reviewed candidate only after CI passes and verify exact source/tree/build provenance and PAPER locks afterward.

## Immediate gates

No current evidence authorizes:

- LIVE;
- real orders;
- capital;
- deployment solely to reconcile SHAs;
- treating raw `up_prob` as a calibrated probability;
- claiming incremental Polymarket edge before the ORDER098 artifacts are exported/verified and ORDER097/099 produce an evaluated causal holdout verdict;
- using HMM/regime complexity to rescue a failed or data-blocked tabular result.

## Merged scientific stack

The research code required for the next falsification experiment is now canonical in `main`:

1. **ORDER098** — GET-only export of persisted T0 audit evidence, manifest/hash lineage, and exact public Polymarket 5m resolution collection.
2. **ORDER097** — exact-target causal nested comparison: market-only versus market+SENEX.
3. **ORDER099** — one-market-one-row weighting, TRAIN-only descriptive cuts, n>=30 exploratory-cell floor, Holm/BH family correction, 10,000-replicate market-cluster bootstrap, practical Brier gain floor 0.005, and a 200-unique-market minimum before any edge verdict.

Historical data access is no longer the blocker: ORDER098/099 were executed. The active constraint is prospective sample accumulation. ORDER100 admits only markets with market_start_ts >= 1791090000 (2026-10-04T05:00:00Z), uses frozen coefficients and disagreement thresholds, and requires at least 300 unique resolved fresh markets in the primary band before any prospective verdict.


## ORDER099 evaluated result

The frozen experiment has now been executed from persisted historical T0 audit
evidence and exact public Polymarket BTC 5m resolutions.

Primary HOLDOUT: 1,169 unique markets.

- market-only Brier = 0.1498170;
- market+SENEX Brier = 0.1494001;
- delta = -0.0004169;
- 95% unique-market bootstrap CI = [-0.0022116, +0.0014253];
- log-loss delta = -0.0008650;
- 95% CI = [-0.0049163, +0.0032143];
- preregistered practical Brier threshold = -0.005.

Verdict: **INCREMENTAL_EDGE_NOT_DEMONSTRATED**.

This is an evaluated null result, not a data blocker. EDGE remains UNPROVEN
and the result does not authorize HMM rescue, deployment, LIVE, orders or
capital.

## ORDER100 prospective state

ORDER100 is merged in `main` and contains no fitting path. A causal smoke run against the historical corpus returned **0 prospective markets**, confirming the cutoff excludes the old sample. The current state is **COLLECTING_PROSPECTIVE_DATA**. No LIVE/capital promotion is authorized by ORDER100 alone.


## REA and OpenSRE integration candidate — 2026-10-15

- REA's Binance public-candle and Grid/DCA/Combo PAPER diagnostic is wired through the public dashboard and GET-only runtime API.
- This integration is complete only for the explicitly triggered PAPER research scope. It is not wired into the authoritative signal path or order executor, and its assumed transaction costs are not a calibration claim.
- OpenSRE is an optional external read-only operations observer through `ops/opensre/h011_readonly_probe.py`. It runs outside H011; no secrets, account access, mutation tools or auto-remediation are included.
- Other research packages and the guarded testnet adapter remain because their status is explicitly research-only/testnet-only, not a proven orphan runtime module.
