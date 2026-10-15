# REA runtime integration status — 2026-10-15

## Scope claimed

The REA Binance path is integrated end-to-end for the explicitly triggered read-only PAPER research diagnostic:

1. The H011 dashboard exposes manual controls for public-candle intake and diagnostic evaluation.
2. `main_real:app` mounts the public Binance router.
3. The router validates the public-data safety contract and discards the open candle.
4. The evaluator runs the packaged deterministic subset of `research/rea_engines`: contracts, accounting, replay, strategies and evaluation.
5. The response reports strategy metrics and buy-and-hold benchmarks, explicit assumed fee/spread/slippage, data freshness/gaps, and safety declarations.
6. Contract tests guard the dashboard, runtime router, Docker packaging and absence of order-execution wiring.

## Explicit non-goals

This is not integration of REA into SENEX's authoritative predictor, risk proposal creation, `ExecutionEngine`, order journal, or Binance account. It uses unsigned public market data and hypothetical fresh ledgers. The offline CLI, historical datasets and deeper evaluation tools remain separate research tools and are not copied into the production image.

Grid/Combo returning zero fills on a short sample is an observed sample result, not proof that the code is inactive or a reason to delete the strategies. Cost values shown by the dashboard are assumptions, not calibrated fills or an exchange-specific fee tier. `EDGE=UNPROVEN` remains mandatory.

## Acceptance gate

- Public route paths are mounted in the production entrypoint.
- UI actions are explicit; no REA request runs on dashboard load or polling timers.
- No account, API-key, signed-request, order, cancel or testnet route is called.
- The output is not routed to the production execution engine.
- The exact runtime commit/tree/build digest and PAPER safety invariants are verified after deployment.

A passing CI run proves code/test acceptance only. It does not prove economic edge or authorize live capital.
