# ORDER096 — iFVG Ultimate+ Deluxe source audit

SOURCE_KIND=USER_SUPPLIED_GOOGLE_DOC
SOURCE_TITLE=Pastebin INFO SENEX
SOURCE_URL=https://docs.google.com/document/d/1A3AxaM1XbNhd_8dUhE8IV244vT1ECNbXAPqCq9sY1DU/edit?tab=t.0
SOURCE_MODIFIED=2026-10-03T22:16:48.813Z
MODE=RESEARCH_ONLY
PRODUCTION_INTEGRATION=NO
EDGE=UNPROVEN

## Why this source matters

The supplied Pine v6 script is not one indicator. It is a confluence framework that combines:

- inversion fair-value gaps (iFVG);
- confirmed liquidity sweeps;
- higher-timeframe FVG delivery;
- lower-timeframe volume-delta proxy;
- cross-symbol SMT/SSMT divergence;
- session liquidity and key levels;
- explicit nearby liquidity targets;
- a six-check grade used to admit setups.

The important question for SENEX is not whether the branded script is popular. It is whether one of those information families is genuinely absent from SENEX and can be evaluated causally without importing the whole stack.

## Source-derived setup logic

The script's setup score is a simple sum of six booleans:

1. recent liquidity sweep;
2. recent HTF FVG/PDA delivery;
3. delta imbalance;
4. IFVG inversion itself;
5. clear liquidity targets;
6. recent SMT with a correlated symbol.

The source then grades 6 checks as A+, 5 as A, 4 as B, otherwise C, with optional filters for bias, macro-time window, sweep requirement, HTF alignment and delta requirement.

This is useful as a decomposition template: test each family independently before any combined grade.

## Causality observations from the supplied source

### Higher-timeframe requests

The HTF FVG helper uses historical offsets (low[1], high[3], time[3]) inside request.security(..., lookahead_on). Previous-day/week levels also use [1] before lookahead_on.

That pattern is materially safer than requesting current unfinished HTF values, but ORDER096 does not assume the TradingView implementation is automatically a valid SENEX T0 source. Any SENEX research reimplementation must lock to fully completed source bars and pass a future-mutation causality test.

### Pivot sweeps

The source uses ta.pivothigh/ta.pivotlow with symmetric left/right lengths, so a pivot becomes knowable only after the right-hand confirmation bars exist. It visually associates the liquidity level with the historical pivot bar.

For SENEX, the event timestamp must be the confirmation/detection time, not the older visual pivot location. Backdating the event to the pivot candle would create lookahead.

### Lower-timeframe delta

The script calls request.security_lower_tf(...) and approximates buy/sell volume by allocating each lower-timeframe candle's volume according to the close's position inside its high-low range.

SENEX already has volume_delta, bidask_imbalance, order-flow and OFI-style information. This lower-timeframe proxy therefore does not justify a second production delta feature.

If researched, it must be frozen at the parent-bar close. Intrabar/realtime values are not interchangeable with historical closed-bar T0 evidence.

## Redundancy against current SENEX

| Source component | Existing SENEX overlap | ORDER096 decision |
|---|---|---|
| lower-TF delta imbalance | HIGH — volume_delta, bidask_imbalance, orderflow, OFI/microstructure | DO NOT ADD |
| session labels / macro windows | HIGH — session/regime enrichment already exists | DO NOT ADD |
| generic ATR strictness | HIGH — volatility/range/risk families already exist | DO NOT ADD |
| volume profile / per-candle value area | MEDIUM-HIGH complexity; no proof of incremental short-horizon BTC information | DO NOT ADD NOW |
| previous day/week/session highs/lows | PARTIAL overlap; price-level context exists elsewhere but not as a canonical EDGE feature | only as part of a sweep hypothesis, not standalone |
| liquidity sweep + reclaim | DISTINCT enough for one cheap causal baseline | KEEP RESEARCH CONCEPT |
| IFVG inversion | DISTINCT price-structure event; no current FVG family found in SENEX | KEEP RESEARCH CONCEPT |
| BTC↔ETH SMT divergence | DISTINCT cross-asset structure; no SMT family found in current SENEX | KEEP RESEARCH CONCEPT |
| six-check grade | NOT a new information source; just combines components | REJECT until components earn weight individually |
| fixed SL/TP/liquidity targets | execution/strategy packaging, not proof of predictive EDGE | RESEARCH ONLY |

## Surgical extraction

ORDER096 should not port the full script.

Only three source ideas survive the redundancy screen:

### IFVG-1 — inversion event

Minimal hypothesis:

> A recently created three-candle fair-value gap that is subsequently invalidated by a close through the opposite boundary carries incremental short-horizon reversal information.

Required:
- completed bars only;
- ATR strictness frozen before evaluation;
- event timestamp = inversion close, not original gap bar;
- compare against plain momentum and volatility regime;
- measure disagreement with SENEX.

### SWEEP-1 — confirmed liquidity sweep / reclaim

Minimal hypothesis:

> Sweeping a previously confirmed swing level and closing back through it contains incremental reversal information.

Required:
- pivot is unavailable until confirmation bars complete;
- event timestamp = sweep/reclaim time;
- no backdating;
- compare against the already-tested previous-4h-range sweep proxy;
- kill if sparse/no incremental information.

### SMT-1 — BTC/ETH divergence

Minimal hypothesis:

> BTC making a new local extreme while ETH fails to confirm it contains incremental reversal information.

Required:
- aligned, closed BTC/ETH bars from consistent venues or an explicit mapping;
- frozen cycle/window;
- no symbol substitution after outcome;
- cross-venue sanity before promotion.

## Cheap exploratory proxy screen

To decide whether these concepts deserve immediate implementation, AUD ran independent causal proxies on recent closed TraderSpy BTC/ETH windows. These proxies are not exact reproductions of the supplied Pine script and are not promotion evidence.

### 15m recent holdout

| Proxy | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| IFVG inversion | 8 | 62.5% | +1.87 bps |
| confirmed swing sweep/reclaim | 7 | 57.1% | -3.96 bps |
| BTC-vs-ETH SMT proxy | 19 | 47.4% | -10.21 bps |

### 1h recent holdout

| Proxy | N | Accuracy | Mean signed next-bar return |
|---|---:|---:|---:|
| IFVG inversion | 22 | 40.9% | -3.37 bps |
| confirmed swing sweep/reclaim | 6 | 50.0% | -9.48 bps |
| BTC-vs-ETH SMT proxy | 26 | 57.7% | +0.59 bps |

Interpretation:

- sample sizes are far too small for inferential claims;
- no concept is stable across 15m and 1h;
- a visually attractive 15m IFVG hit rate does not survive the 1h screen;
- the SMT proxy flips from poor 15m to mildly positive 1h;
- sweep/reclaim is sparse and economically weak in this tiny window.

Therefore the source adds research hypotheses, not production features.

## Final source disposition

PORT_FULL_IFVG_SCRIPT=NO
ADD_SOURCE_DELTA_FEATURE=NO
ADD_SESSION_FEATURE=NO
ADD_VOLUME_PROFILE=NO
ADD_CONFLUENCE_GRADE=NO

KEEP_IFVG_INVERSION_HYPOTHESIS=YES
KEEP_CONFIRMED_SWEEP_HYPOTHESIS=YES
KEEP_BTC_ETH_SMT_HYPOTHESIS=YES

IMMEDIATE_PRODUCTION_CHANGE=NO
IMMEDIATE_T0_CHANGE=NO
EDGE=UNPROVEN

The next valid use of these concepts is the same paired T0 disagreement framework defined by ORDER096. If they do not add information specifically when SENEX/simple baselines disagree, they are killed.