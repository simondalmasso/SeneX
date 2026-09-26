# GPTrader call and payload budget — ORDER085

Assumptions:
- 15-minute nominal prediction cycle
- two symbols
- 24 hourly ChatGPT runs/day
- batch limit 16
- packet target <=4 KiB, hard bounded projection <=8 KiB

## Target steady state

| Call | /run | /day |
|---|---:|---:|
| get_gptrader_health | 1 | 24 |
| get_prediction_batch | 1 | 24 |
| submit_paper_decisions | 1 | 24 |
| total Decision MCP | 3 | 72 |
| direct GPTrader D1 reads | 0 | 0 |
| direct GPTrader D1 writes | 0 | 0 |
| public H011 reads by task | 0 | 0 |

Nominal batch ≈8 packets/hour.

Compared with 192 one-poll-per-prediction requests/day, hourly batching reduces external polling by 87.5%.

## Catch-up

24h optional-overlay outage may leave ~192 packets. At 16/hour, drain time is ~12h. Deterministic baselines continue without waiting for ChatGPT.

## Payload control

Project bounded decision features only. If a packet exceeds hard budget, remove nonessential OHLCV/detail and retain identity/hash/decision features. Never broaden to remote history scans.

No OFFSET, no COUNT(*), no broad history hydration.
