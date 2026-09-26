# GPTrader implementation plan — FUTURE ORDER086

ORDER085 is design-only. This file is a decomposition, not implementation authorization.

## P0 — T0 packet sealer

Future package:
`senecio_polymarket/backend/gptrader/`

Add schemas, paths, sealer and cursor. Hook after prediction persistence using the same non-fatal additive pattern as existing PAPER routing.

Tests: denylist, hash stability, refuse outcome-bearing source, volume path.

## P1 — deterministic policies + L1/L2

Implement fixed-risk baselines and dependence-aware diagnostics. Evidence gate: >=600 independent 1h resolved and >=14 days. Raw up_prob remains an uncalibrated score.

## P2 — namespaced PAPER coordinator

Reuse PortfolioCoordinator/RiskKernel/ExecutionEngine/TradeJournal classes with separate cash, positions and journal under results/gptrader. Do not modify the SENEX singleton.

## P3 — observational APIs/dashboard

Add GET-only GPTrader state/trades/verdict endpoints and a separate PAPER/HYPOTHETICAL dashboard section. UNKNOWN remains UNKNOWN.

## P4 — Decision MCP

Preflight Northflank dedicated service/sidecar first. Fallback: dedicated Cloudflare Worker without D1. Never public mutating H011.

Implement health, batch, decision-safe state, idempotent submit.

## P5 — ChatGPT runbook

Decision MCP only. No web/Review MCP/dashboard/crypto tools. Primary treatment is fixed-risk TAKE/ABSTAIN. Persist decision before any replay/settlement read.

## P6 — Verdict engine

Implement exact enum and dependence-aware rules from DESIGN.md / EXPERIMENT.md.

## P7 — optional Review MCP

Separate connector/token for settled results.

## Rollback

Disable sealer/GPTrader hooks, stop sidecar and remove/ignore namespaced GPTrader runtime state. SENEX control book remains untouched.

No LIVE, D1 schema, wallet or model tuning in ORDER086 unless a later explicit owner instruction changes scope.
