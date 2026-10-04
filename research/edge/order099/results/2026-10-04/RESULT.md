# ORDER099 evaluated result — 2026-10-04

## Verdict

`INCREMENTAL_EDGE_NOT_DEMONSTRATED`

The preregistered comparison did not demonstrate a practically meaningful or
statistically robust incremental contribution from frozen SENEX T0 information
beyond the contemporaneous Polymarket BTC Up/Down 5m market prior.

## Corpus and admissibility

- Projected T0 rows: 3,570
- Exact resolution markets requested/accepted: 3,568 / 3,568
- Resolution rejections: 0
- Anti-circularity exclusions: 27 rows
- Admissible unique markets: 3,541
- TRAIN unique markets: 2,372
- HOLDOUT unique markets: 1,169
- Bootstrap: 10,000 unique-market resamples, seed 7

The 27 exclusions were rows where
`polymarket_context_v1.directional_use` was not explicitly `False`.
They were excluded so the comparison could not credit SENEX for information
already consumed from the Polymarket prior.

## Primary nested holdout

| Metric | market-only | market+SENEX | Delta augmented-minus-market |
| --- | ---: | ---: | ---: |
| Brier | 0.1498170 | 0.1494001 | -0.0004169 |
| Log loss | 0.4666161 | 0.4657511 | -0.0008650 |
| Directional accuracy | 82.036% | 81.095% | -0.941 pp |

Bootstrap 95% interval for Brier delta:
`[-0.00221158, +0.00142532]`.

Bootstrap 95% interval for log-loss delta:
`[-0.00491631, +0.00321430]`.

The preregistered practical Brier improvement required for `EDGE_SUPPORTED`
was at least `-0.005`. The observed mean improvement was about one-twelfth
of that magnitude and both uncertainty intervals crossed zero.

## Secondary subgroup findings

Five exploratory subgroup cells survived both Holm and BH correction. These
findings do **not** override the primary result.

The strongest adverse descriptive pattern was the highest absolute-
disagreement quartile, where adding SENEX worsened mean Brier by about
`+0.0100`. This argues against a naive rule such as "trade when SENEX strongly
disagrees with the market".

Any subgroup follow-up requires a new preregistered prospective experiment on
fresh untouched data.

## Promotion decision

- EDGE remains `UNPROVEN`.
- Do not enable LIVE or real capital.
- Do not use HMM/regime complexity to rescue this result.
- Null is an admissible successful scientific outcome.

Exact artifact hashes and primary metrics are in `RUN_MANIFEST.json`.
The full local machine-readable report has SHA256
`d7efa83c5985339bdc9788389640b9c749d055742008e54fc95d3edee7b273ba`.
