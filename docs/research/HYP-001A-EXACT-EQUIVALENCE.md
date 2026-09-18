# HYP-001A — Exact 1h Contract Equivalence Audit

Status: **REJECT — current contracts are not exactly equivalent.**

This is a contract audit, not a performance test. No directional baseline, Brier, LogLoss, ECE, EV or PnL comparison is authorized by this result.

## Official Polymarket BTC Up/Down Hourly contract

Evidence source: official Polymarket Gamma public API, series `10114` (`btc-up-or-down-hourly`), plus Polymarket's official market rules.

Observed live/upcoming contracts are clock-hour markets. Example:

- title: `Bitcoin Up or Down - September 18, 1AM ET`
- event start: `2026-09-18T05:00:00Z`
- event end: `2026-09-18T06:00:00Z`
- asset/pair: `BTC/USDT`
- resolution source: Binance BTC/USDT
- resolution: compare finalized Binance **1H candle close vs open** for the candle beginning at the titled hour
- Up: `close >= open`
- Down: `close < open`
- tie semantics: equality resolves **Up**
- title timezone: ET; API timestamps are explicit UTC instants

The research snapshot contains eight consecutive official hourly markets and condition IDs.

## Current SENEX authoritative 1h contract

Source code audited at research base `f58e2f3e5555b954a93b988225036b65b60ac9b8`.

- asset: normalized `BTC/USDT` for BTC rows
- origin: persisted prediction timestamp + persisted `price_now`
- end target: **origin timestamp + exactly 3600 seconds**
- target evidence: close of the 1m candle containing the exact target instant
- source: persisted `exchange_used`, normalized against current allowlist:
  `okx, kraken, gate, mexc, bitget`
- Binance is not in the authoritative settlement allowlist
- LONG WIN: `later > origin`
- SHORT WIN: `later < origin`
- equality: neither direction wins; LONG and SHORT both evaluate LOSS on equality
- internal timestamp normalization: UTC
- authoritative sample: deterministic non-overlapping 1h rows after proof qualification

## Exact-equivalence matrix

| Dimension | Polymarket hourly | SENEX authoritative 1h | Exact? |
| --- | --- | --- | --- |
| Asset | BTC/USDT | BTC/USDT | YES |
| Horizon length | 3600s | 3600s | YES |
| Start anchor | fixed clock-hour candle open | exact prediction timestamp | NO in general |
| End anchor | fixed clock-hour + 3600s | prediction timestamp + 3600s | NO in general |
| Timezone | ET label, UTC API instant | UTC | compatible only after instant conversion |
| Data source | Binance BTC/USDT | same persisted non-Binance source from allowlist | **NO** |
| Resolution | finalized Binance 1H close vs open | t+3600 price vs prediction-time price using 1m evidence | **NO** |
| Tie | Up | neither directional side wins | **NO** |

Even if a SENEX prediction happened exactly on a Polymarket hour boundary, source and tie/resolution semantics still differ. Therefore current SENEX 1h outcome and Polymarket BTC hourly outcome are **not the same label**.

## Consequence

The requested prospective SENEX-vs-Polymarket directional baseline is **blocked by contract non-equivalence** under the exact-equivalence requirement. No collector that pretends these are interchangeable outcomes is created.

A future research order could define a new Polymarket-aligned SENEX shadow label (Binance, clock-hour, `close >= open`) without altering CORE, but that would be a new external research target, not the current authoritative SENEX 1h outcome.
