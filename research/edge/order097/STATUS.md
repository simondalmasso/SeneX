# ORDER097 — Current Status

```text
ROLE=AUD
ORDER=ORDER097-AUD
MODE=OFFLINE_RESEARCH_ONLY
BASE_MAIN=a181dc901d02664345124ad08b4079a7114b0707
BRANCH=order097/market-prior-incremental-edge
EDGE=UNPROVEN

T0_MARKET_PRIOR_CAPTURE=EXISTS
T0_SENEX_RAW_SCORE=EXISTS
POLYMARKET_5M_IDENTITY=EXISTS
CANONICAL_SENEX_15M_1H_LABELS=EXIST
POLYMARKET_5M_RESOLUTION_LABELS=NOT_PERSISTED
PUBLIC_ENDPOINT_RICH_T0_AUDIT=NOT_EXPOSED

STATUS=BLOCKED_TARGET_LABEL_5M_NOT_PERSISTED

PREDICTOR_MUTATIONS=0
GPTRADER_MUTATIONS=0
RUNTIME_MUTATIONS=0
DEPLOYMENTS=0
LIVE=NO
REAL_ORDERS=0
CAPITAL=0
```

## What is implemented

The research harness can:

- extract only valid decision-time SENEX/Polymarket pairs;
- reject circular rows where SENEX already consumed Polymarket directionally;
- enforce the exact BTC 5m market grid;
- ignore normal SENEX 15m/1h outcome fields;
- join independently supplied 5m resolutions after market close;
- preserve Phase 0 totals for all rows, valid pairs, rejected pairs, and unique markets;
- reject missing, blank, or non-string resolution provenance;
- split chronologically by complete market identity and purge TRAIN labels not available before the earliest HOLDOUT decision;
- keep insufficient resolution/training corpora in an explicit blocked state rather than raising an uncaught calibration error;
- fit market-only and market+SENEX logistic models on identical TRAIN rows;
- evaluate those nested models on identical HOLDOUT rows so base-rate/intercept fitting cannot masquerade as incremental SENEX information;
- return explicit blockers when labels are absent or scientifically insufficient.

## What is not being built

- no new predictor;
- no new Polymarket adapter;
- no second execution engine;
- no indicator stack;
- no GPTrader integration;
- no live order path;
- no production endpoint merely to make the research easier.

## Exact next evidence dependency

Obtain a reproducible set of Polymarket BTC Up/Down 5m resolution records for the slugs/condition IDs already present in the **local** H011 prediction journal.

That evidence can be generated offline/post-close. It must not modify T0 records.

Until that set exists, the correct answer is a blocker rather than an EDGE statistic.
