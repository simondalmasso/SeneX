# HYP-008 — SENEX-POLYMARKET-SHADOW

Status: **DESIGNED_NOT_RUN / NOT_PROMOTED**.

Purpose: evaluate SENEX-derived directional signals against the exact Polymarket BTC Up/Down Hourly label without pretending the current SENEX authoritative 1h settlement is equivalent.

## Exact external target

For each eligible Polymarket BTC Hourly contract:

- asset: BTC/USDT
- outcome source: Binance BTC/USDT
- signal capture instant: exact market `eventStartTime` / clock-hour boundary
- label start: Binance finalized 1H candle open at that exact instant
- label end: final close of that same 1H candle
- `UP` iff `close >= open`
- `DOWN` iff `close < open`
- tie semantics: **UP**
- event timestamps and Binance candle timestamps must identify the same UTC instants

## SENEX shadow signal

At the exact boundary, capture a research-only SENEX-derived signal using only information available at or before that instant.

To avoid target contamination:

- Polymarket directional signal injection is disabled for this shadow evaluation
- no future candle data may enter the feature snapshot
- no current SENEX native settlement label is reused as the Polymarket label
- the external Binance label is attached only after the 1H candle finalizes

The shadow record must bind code/config/effective-weight and feature-policy hashes just like HYP-001C.

## Isolation

HYP-008 may get its own future append-only collector under EDGE LAB. It must not:

- import EDGE LAB into SENEX runtime
- mutate SENEX CORE
- change the production prediction cadence to clock-hour boundaries
- change settlement authority
- write back a score or feature to production
- create orders, LIVE authorization or capital movement

Current state: design only. No HYP-008 observations have been collected and no performance metric has been computed.

EDGE remains UNPROVEN.
