# ORDER097 — Target Alignment Contract

## The problem

SENEX currently carries two different time targets in the same decision-time audit:

- `pipeline.step2_features.up_prob`: a raw SENEX directional score whose probability semantics are explicitly UNVALIDATED;
- `external_markets_v1.polymarket.up_probability`: the observable prior of the current BTC Up/Down **5-minute** Polymarket contract.

Canonical SENEX settlement/authority currently evaluates 15m and 1h outcomes, with 1h as the primary scientific authority. Those outcomes are **not** the same random variable as the 5m Polymarket contract.

Therefore this comparison is forbidden:

```text
Polymarket p(UP over 5m)  vs  SENEX 1h WIN/LOSS
```

It can generate apparently precise Brier/log-loss/edge numbers while answering the wrong question.

## Admissible comparison

ORDER097 permits only:

```text
same decision timestamp T0
same BTC Up/Down 5m market
same slug
same condition_id
same [start_ts, end_ts) 300-second grid
decision before market end
Polymarket prior captured at T0
SENEX raw score captured at T0
5m market resolution observed after market end
```

The SENEX row's normal `outcome` field is never used as the 5m market label.

## T0 source

The only admissible historical market prior is:

`_audit.external_markets_v1.polymarket.up_probability`

with:

- `source=POLYMARKET_PUBLIC`
- `version=polymarket-btc-5m-v1`
- `eligible_for_prediction=true`
- valid `slug`, `condition_id`, `start_ts`, `end_ts`

No live adapter fallback may be substituted for a missing historical value.

The SENEX score is:

`_audit.pipeline.step2_features.up_prob`

It is treated as a score in [0,1], **not** as an already calibrated probability.

## Resolution source contract

A resolution record must contain:

```json
{
  "slug": "btc-updown-5m-<start_epoch>",
  "condition_id": "<exact condition id>",
  "start_ts": 0,
  "end_ts": 0,
  "outcome": "UP|DOWN",
  "resolved_at": "ISO8601 UTC",
  "source": "<resolution evidence source>"
}
```

Requirements:

- `end_ts - start_ts = 300`
- slug epoch equals `start_ts`
- exact slug+condition identity
- `resolved_at >= end_ts`
- outcome is binary UP/DOWN

Conflicting evidence for the same identity raises an error. Missing labels produce a blocker, not a synthetic label.

## Current repository finding

As of main `a181dc901d02664345124ad08b4079a7114b0707`:

- decision-time Polymarket identity/prior is already captured locally;
- public authority projection intentionally omits the rich external-market audit;
- canonical SENEX settlement persists 15m/1h labels;
- the repo does **not** persist the Polymarket 5m contract resolution needed for target-aligned scoring.

Current scientific state:

`BLOCKED_TARGET_LABEL_5M_NOT_PERSISTED`

This is a data-contract blocker, not a request for another trading engine.
