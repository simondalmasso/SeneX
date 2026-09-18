# HYP-002 — Native LONG vs SHORT Directional Analysis

Status: **INCONCLUSIVE / historical diagnostic only**.

Source: bounded public read-only SENEX `/api/oracle/predictions/db?limit=50&symbol=BTCUSDT` snapshot observed after HYP-001C registration work began.

Selection:

- 50 public rows
- 25 passed the current proof-qualified settlement contract
- 10 remained after deterministic non-overlap 1h selection
- all 10 predate the HYP-001C registration instant and therefore are `DIAGNOSTIC_ONLY_HISTORICAL`
- prospective frozen authority candidates: 0

## Historical diagnostic result

| Direction | N | Wins | Losses | Win rate | Wilson 95% |
| --- | ---: | ---: | ---: | ---: | --- |
| LONG | 3 | 2 | 1 | 66.67% | 20.77%–93.85% |
| SHORT | 7 | 2 | 5 | 28.57% | 8.22%–64.11% |

These figures are small-N diagnostics, not authority, not a ranking claim, and not evidence of stable directional edge.

The authoritative HYP-002 result remains `INCONCLUSIVE` until HYP-001C accumulates frozen post-registration `AUTHORITY_CANDIDATE` rows.

No Brier, LogLoss, ECE, economic edge or production promotion is authorized.
