# HYP-001C — Prospective SENEX-Native Frozen Cohort

Status: **REGISTERED / COLLECTOR ACTIVE / AUTHORITY N=0**.

Registration instant: `2026-09-18T04:20:39.618657+00:00`.

This is EDGE LAB only. It reads already-persisted SENEX observations and appends research records. It has no SENEX database write path and does not alter runtime behavior.

## Frozen decision identity

- source runtime commit: `a4db139111d77db369bf8c103c8934da15a6e382`
- `code_hash=f6e99ae3add8f016979113ac5a7d67415085e037630588b079bf389f937eadf4`
- `config_hash=93ae6ed9f4d249417183531195793a97164a132b79d7729f6cf7b3ab693198b0`
- `effective_weights_hash=73780e5cdc6bd15448d605a3fd69e272dc08920b7da97c560a6b1e20911a0fff`
- feature policy: `missing-input-mask-v1`, with missing inputs excluded from pressure and agreement denominator
- exchange policy: `EXACT_PERSISTED_EXCHANGE_NO_DEFAULT_NO_INFERENCE`
- frozen exchange: `okx`

A future row is not an authority candidate if any frozen identity changes. A new cohort/version is required instead of pooling regimes.

## Persisted fields

Each selected record preserves:

- raw `step2_features.up_prob` unchanged
- action, feature direction and final prediction
- prediction timestamp
- `exchange_used`
- `price_now`
- exact proof-qualified t+3600 settlement price
- complete 1h historical-price evidence
- settlement observation
- native strict-up label `Y_up = 1 iff price_1h_later > price_now, else 0`
- native directional outcome from `outcomes_dual.outcome_1h`
- observed freeze hashes/policy
- cohort status and exclusion reasons

Brier/LogLoss are blocked.

## Non-overlap

Only a prospective proof-qualified row that is at least 3600 seconds after the prior authority candidate can receive `AUTHORITY_CANDIDATE`.

Pre-registration rows may be retained only as `DIAGNOSTIC_ONLY_HISTORICAL`. Rows failing freeze or overlap gates can never silently enter authority.

## Current seed state

Public read-only SENEX endpoint snapshot:

- rows observed: 50
- proof-qualified: 25
- independent historical 1h rows retained: 10
- prospective authority candidates: 0

The historical rows exist only to exercise the native evidence path and HYP-002 diagnostic analysis. They cannot establish edge.
