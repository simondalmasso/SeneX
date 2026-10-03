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
- enforce the exact BTC 5m market grid;
- ignore normal SENEX 15m/1h outcome fields;
- join independently supplied 5m resolutions after market close;
- split chronologically by complete market identity;
- fit a minimal train-only Platt calibration;
- evaluate calibrated SENEX vs market prior on identical holdout rows;
- return an explicit blocker when labels are absent.

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
