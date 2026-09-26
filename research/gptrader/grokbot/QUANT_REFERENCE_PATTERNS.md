# External Quant Reference Patterns for GPTrader

Status: RESEARCH_REFERENCE_ONLY  
Applies to: ORDER085 / future ORDER086 implementation review  
Runtime dependency decision: NONE

This note preserves the useful engineering and research patterns from external quant repositories without importing their frameworks into SENEX.

## Decision rule

Prefer:

`BORROW_TESTS_AND_METHODS > IMPORT_FRAMEWORKS`

SENEX already owns:
- prediction generation;
- `PortfolioCoordinator`;
- `RiskKernel`;
- `ExecutionEngine`;
- `TradeJournal`;
- PAPER safety and runtime provenance.

Adding another trading framework as runtime authority would make attribution and validation harder.

---

## 1. mementum/backtrader

Repository: https://github.com/mementum/backtrader

Use: **reference / test oracle only**

Do not:
- vendor the framework;
- replace SENEX `ExecutionEngine`;
- introduce Backtrader as a second broker simulator authority;
- use its live-broker capabilities.

Patterns worth borrowing:
- explicit commission models;
- explicit slippage models;
- broker simulator semantics;
- order/fill edge cases;
- replay/resampling test cases;
- cash/position accounting invariants;
- analyzers as post-trade diagnostics.

Why not import:
- SENEX already has equivalent ownership boundaries;
- duplicate execution engines would confound GPTrader-vs-SENEX attribution;
- latest visible upstream commits inspected for this review were from 2023, so treat it as a mature reference, not canonical runtime authority.

Potential ORDER086 use:
- convert selected slippage/commission/fill semantics into SENEX-native regression tests.

---

## 2. je-suis-tm/quant-trading

Repository: https://github.com/je-suis-tm/quant-trading

Use: **deterministic research baseline ideas only**

The repository itself states many scripts assume frictionless trading:
- no slippage;
- no surcharge;
- no illiquidity.

Therefore its PnL examples are not suitable as evidence for SENEX.

Patterns worth borrowing:
- simple momentum baseline;
- pair/stat-arb examples;
- technical-rule baselines;
- Monte Carlo/portfolio examples as educational references.

Potential ORDER086/later use:
- port only very simple rules as deterministic benchmark policies;
- always run them through SENEX's own fees/slippage/risk assumptions.

Do not import scripts wholesale.

---

## 3. stefan-jansen/machine-learning-for-trading

Repository: https://github.com/stefan-jansen/machine-learning-for-trading

Use: **high-value methodology reference**

This is the strongest external reference of the four for GPTrader.

Patterns worth borrowing:
- explicit evidence boundary between exploration/tuning and confirmation;
- walk-forward evaluation;
- leakage-safe dataset construction;
- transaction-cost modeling as a first-class concern;
- risk and execution separated from raw predictive signal;
- multiple-testing controls;
- Deflated Sharpe Ratio where semantically applicable;
- strategy pause/retire logic when edge decays;
- monitoring and governance around model/strategy drift.

Potential future dev/research libraries may be evaluated individually, but ORDER086 does not require importing the full ML4T stack.

Key SENEX mapping:

```text
ML4T evidence boundary
        ↓
SENEX sealed T0 packets
        ↓
GPTrader decision commit
        ↓
later replay / settlement
```

---

## 4. wilsonfreitas/awesome-quant

Repository: https://github.com/wilsonfreitas/awesome-quant

Use: **discovery index only**

This is a catalog, not an implementation dependency.

Rules:
- no package enters SENEX merely because it appears in Awesome Quant;
- every candidate must be independently checked for maintenance, license, dependencies, overlap and test value;
- prefer standard-library / existing-SENEX capability when equivalent.

---

## 5. Unified alpha extraction / signal-combination framework supplied by owner

Use: **future research baseline, not ORDER086 model tuning**

Interesting low-level concepts:
1. time-series demeaning;
2. variance normalization;
3. cross-sectional demeaning;
4. orthogonalization/residual extraction against common factors;
5. inverse residual-volatility weighting;
6. absolute-weight normalization before signal combination.

Potential future architecture:

```text
raw SENEX sub-signals
        ↓
standardize
        ↓
remove common component
        ↓
residualize against preregistered factors
        ↓
weight by residual risk
        ↓
combined diagnostic signal
```

Why deferred:
- ORDER086's purpose is to evaluate existing SENEX predictions, not change them;
- with only BTC/ETH and limited independent signal dimensions, cross-sectional residualization may add complexity without identification benefit;
- signal-combination research should begin only after sufficient sealed data exists and the existing signal's usefulness is measured.

This method must therefore live in a later research order with:
- preregistered factors;
- no-lookahead transforms;
- walk-forward estimation;
- held-out confirmation;
- multiple-testing controls.

---

## 6. External market tools

CoinMarketCap, TradingCursor, Binance, Bybit, CoinGecko, Exum and similar tools are **review/research-only** for the GPTrader experiment.

They must not be connected to the scheduled GPTrader Decision task because they can expose post-T0/current-market information.

Decision surface:

```text
sealed T0 packet -> Decision MCP -> TAKE/ABSTAIN
```

Review surface may use external current-market tools only after the decision is durably committed.

---

## Final adoption matrix

| Source | Runtime dependency | Borrow now | Defer |
|---|---|---|---|
| Backtrader | No | slippage/commission/fill test patterns | framework |
| quant-trading | No | simple baseline ideas | direct PnL claims |
| ML4Trading | No full-stack import | methodology / evidence boundary / leakage controls | optional libraries after audit |
| Awesome Quant | No | discovery only | all package adoption until independently justified |
| Alpha-combination framework | No | preserve as research candidate | actual signal combination/tuning |

The objective is to increase scientific rigor without creating a second trading platform inside SENEX.
