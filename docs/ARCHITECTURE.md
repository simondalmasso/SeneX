# SENEX — Architecture

This document describes the current repository and the deployed H011 topology at a presentation-ready level.

## 1. Runtime surfaces

### Public H011 application

Entrypoint: `senecio_polymarket.backend.main_real:app`.

Properties:

- observational/read-only;
- middleware allows only GET, HEAD and OPTIONS;
- public OpenAPI currently contains GET operations only;
- uses one cached authority snapshot per symbol;
- publishes health, readiness, provenance, oracle, PAPER and REA/Binance public-research observation surfaces;
- does not mount the private admin application.

### Private admin application

Module: `backend/admin.py`.

It is a separate bearer-authenticated control application with OpenAPI disabled. It is not mounted by the production public launcher.

### T0 audit receipts

Module: `backend/t0_audit/`.

The oracle seals an immutable decision-time packet before recording its dedupe marker and routing any PAPER simulation. Optional replication is HTTPS-only and can be disabled by leaving its dedicated URL/token unset. This package contains no decision model, paper book, external LLM adapter or order API. Historic packet IDs/schema version and the data directory remain stable solely for audit continuity.

## 2. Scientific data flow

```mermaid
flowchart TD
    MD[Public market data] --> OR[Oracle Runner]
    OR --> PRED[Prediction + T0 audit]
    PRED --> SET[Settlement contract]
    SET --> PROOF[Settlement proof gate]
    PROOF --> COHORT[Independent non-overlap 1h cohort]
    COHORT --> SCORE[Authoritative score]
    SCORE --> SNAP[Atomic authority snapshot]
    SNAP --> API[Public GET API]
    SNAP --> DASH[Dashboard]
```

The primary canonical settlement target is 1h, with 15m retained as a diagnostic secondary window. Historical settlement requires bounded public-candle evidence; raw WIN/LOSS rows are not automatically proof-qualified.

## 3. Portfolio/PAPER flow

```mermaid
flowchart TD
    P[Trade proposal] --> R[RiskKernel]
    R -->|approved| E[ExecutionEngine]
    E --> F[Fill simulation]
    F --> J[TradeJournal]
    F --> S[ShadowLive]
    J --> A[PortfolioAnalytics]
    A --> L[LiveGate diagnostics]
    S --> L
    L -->|structural override| LOCK[HARD_PAPER_LOCK]
```

The hard PAPER lock is a code constant and rejects attempts to activate live execution through environment variables, configuration mutation, LiveGate output or direct engine calls.

## 4. Exchange capability boundary

`oracle/exchange_connector.py` is primarily a public-market-data connector. It also contains an explicitly guarded Binance **testnet** order method used for execution experiments.

Defense in depth includes:

- `binance_testnet` exchange identity;
- `BINANCE_TESTNET_KEY/SECRET` only;
- `set_sandbox_mode(True)`;
- testnet URL verification before order creation;
- CI checks that mainnet credential names are absent from that capability.

This is a testnet capability in the repository, not evidence of a production live-order path.

## 5. REA PAPER research path

The public dashboard offers explicit user-triggered calls to the Binance public-candle and REA PAPER diagnostic routes. Closed candles are validated, then passed to deterministic Grid/DCA/Combo evaluators with separate buy-and-hold benchmarks and explicit assumed costs. No output is routed to the production `ExecutionEngine`, an account endpoint or an order API. The short recent sample is diagnostic only; `EDGE=UNPROVEN`.

## 6. OpenSRE operations observer

`ops/opensre/h011_readonly_probe.py` collects a bounded health/readiness/provenance/score/LiveGate snapshot using GET requests against the allowlisted H011 host. The result can be reviewed through OpenSRE outside the application process. OpenSRE is optional, external and public-alpha; no automatic remediation or runtime credentials are configured by this integration.

## 7. Research layer

`backend/research/` contains reusable research utilities including calibration, purged CV, statistical validation, stress testing, Monte Carlo, drift detection and walk-forward analysis.

ORDER097/098/099 are deliberately outside the production predictor path and are now merged research tooling. ORDER098 exports/verifies frozen decision-time T0 evidence and exact public Polymarket 5m resolutions; ORDER097 compares equivalently train-fitted market-only versus market+SENEX models on the exact same BTC Up/Down 5m target and causal holdout rows; ORDER099 adds preregistered tabular falsification, one-market-one-row weighting, multiple-testing controls and unique-market cluster-bootstrap uncertainty. None of these modules is imported by H011 runtime execution.

## 8. CI/release boundary

Canonical CI checks:

1. exact checkout and lineage;
2. locked dependencies;
3. authority/readiness tests;
4. full regression;
5. PAPER/network safety;
6. frontend truth;
7. public OpenAPI method boundary;
8. compile/static-delta checks;
9. canonical Docker build;
10. non-root image;
11. fail-closed container smoke with network disabled.

A green CI run certifies the tested candidate against those gates. It does not authorize merge, deployment, LIVE or capital.

## 9. Repository presentation policy

For clarity, documentation should distinguish four statuses:

- **DEPLOYED** — observed on H011;
- **MAIN** — merged GitHub code;
- **RESEARCH** — branches/experiments not merged;
- **HISTORICAL** — preserved evidence/canon that no longer describes current state.

This status vocabulary avoids the main source of confusion found during the formal audit.
