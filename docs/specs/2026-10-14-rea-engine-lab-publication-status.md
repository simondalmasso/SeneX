# REA Engine Lab — publication status

Date: 14 October 2026
Repository: simondalmasso/SeneX
Work branch: feat/rea-engine-lab-20261014
Base: 583e11588cea8594db8625a01dc0305ec826f008 (main at the start of this work)

## Delivered source scope

- Decimal accounting, typed strategy contracts, deterministic historical replay, Grid/DCA/Combo clean-room strategy approximations, and offline evaluation tooling.
- Isolated paper execution contract with hash-chained journal, fsync-before-commit, cancellation/expiry terminal semantics, checkpoint recovery and reconciliation freezes.
- Causal BTC/ETH market-regime classifier and focused regression tests.
- Public Binance Spot market-data GET routes and a paper diagnostic surface; routes do not use credentials or call account/order endpoints.
- Dashboard research/diagnostic panels and a snapshot export with source/artifact integrity checks.
- Design/implementation specifications, architecture matrices, methodology and test documentation.

Commercial engines remain public-surface/documentation observations only. No vendor private engine was recovered, and no profitability claim is made.

## Verification

| Area | Latest recorded result |
|---|---:|
| Full repository pytest under temporary test-only import shim | 696 passed, 93 warnings |
| Dashboard snapshot tests | 9 passed |
| Public Binance/data/router/intake + durable paper contract bundle | 51 passed |
| Independent Node paper bot | 32 passed |
| Clean-room engine families | 31 passed |
| Market regime classifier | 4 passed |
| git diff --check | exit 0 |

The full pytest command was python -m pytest -q --disable-warnings, using the existing local Python 3.14 interpreter and a temporary sitecustomize.py outside the repository. It supplied only the missing sse_starlette.sse.EventSourceResponse import by aliasing it to Starlette StreamingResponse, solely to permit collection and test execution. The shim was removed and is not committed. This does not verify actual SSE streaming behavior. The unshimmed environment needs the dependency already pinned in the runtime lock to run the health/readiness suite normally.

## Explicit exclusions

The update intentionally does not commit:

- Raw ETHUSDT candles or the downloaded monthly ETH source archives. The local data/binance/DATA_USE_NOTICE.md records a non-commercial, personal and non-production use boundary; no broader rights are assumed.
- Large generated historical backtest/reproduction JSON and CSV artifacts, Python bytecode, or .bak snapshots.
- The temporary test shim or workstation-specific SHA inventory covering excluded local artifacts.

Methodology, key hashes and limitations are preserved in TEST_REPORT_2026-10-14.md and MARKET_REGIME_CLASSIFICATION_2026-10-14.md. To reproduce, provide local market data obtained under appropriate terms and verify its exact SHA-256 before use.

## Safety and open gates

- PARTIAL_RESEARCH_COMPLETE
- PAPER_ONLY
- EDGE=UNPROVEN
- Production/runtime order integration: NOT APPROVED
- No deployment, merge to main, account credentials, authenticated account calls, testnet/live orders, or commercial-engine integration was performed.
- Container deployment, real SSE transport, and deployed browser rendering still require verification in a provisioned environment with the locked dependencies.
