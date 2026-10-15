# REA Engine Lab Implementation Plan

> **For agentic workers:** Execute task-by-task in the isolated local branch. Steps use checkbox syntax for tracking.

**Goal:** Implement a deterministic, offline, spot-only historical replay lab for the REA Grid, DCA, and Combo strategy approximations, with auditable accounting and cost-aware reports.

**Architecture:** Keep all new code under `research/rea_engines/`; it must not be imported by the SENEX runtime. Use immutable dataclasses and Decimal ledger values, with a single event replay that handles market/limit orders and a separate strategy protocol. Evaluation consumes explicit local CSV paths only and emits provenance-bound JSON.

**Tech Stack:** Python standard library only (dataclasses, Decimal, csv, hashlib, json, unittest). No new packages or network access.

## Global Constraints

- Work only in the isolated local clone and branch `feat/rea-engine-lab-20261014`; no remote writes, push, merge, PR, deployment, or CI trigger.
- Keep `ZERO_SPEND=HARD`, `RESEARCH_ONLY=true`, `PAPER_ONLY=true`, `LIVE=false`, `REAL_ORDERS=0`, `CAPITAL=0`, `NO_KEYS=true`.
- Do not modify runtime, predictors, `BINANCE_SIM`, frozen ORDER097/098/099/100 evidence, H011, or any existing order/branch.
- No network calls, account access, API keys, exchange SDKs, CCXT, testnet, leverage, margin, futures, shorts, or borrowing.
- Candidate results never establish an edge on their own; every report retains `EDGE=UNPROVEN`.
- No proprietary engine claims: Grid/DCA/Combo are clean-room approximations from public concepts and existing REA code.
- Existing SENEX tests import pytest, but the SentinelX Python environment currently lacks pytest. New focused tests must use standard-library unittest. Recheck for a local pytest-capable interpreter or cached wheel without network access; if unavailable, report the full-suite limitation precisely.

---

### Task 1: Contracts and Decimal accounting ledger

**Files:**
- Create: `research/rea_engines/__init__.py`
- Create: `research/rea_engines/contracts.py`
- Create: `research/rea_engines/accounting.py`
- Create: `tests/test_rea_engine_accounting.py`

**Interfaces:**
- `Candle(timestamp, open, high, low, close, volume)` stores timestamp and numeric inputs as explicit, validated values.
- `OrderIntent(side, order_type, quantity, limit_price=None, expires_after_bars=None, tag="")`
- `CostProfile(fee_bps=10, spread_bps=5, slippage_bps=5, profile_id="rea-base-v1")`
- `Ledger(starting_cash, max_inventory, max_fill_qty)` exposes `buy`, `sell`, `equity(mark_price)`, and a deterministic metrics snapshot.
- Ledger returns explicit fee/cost values; it never allows negative cash or inventory.

- [x] **Step 1: Add focused failing tests**
  - Test buy then mark-to-market equity includes fee once.
  - Test weighted-average entry after two buys and realized PnL after a partial sale.
  - Test overspend, selling more than inventory, and exceeding per-fill cap fail closed.
  - Test invalid prices and negative volume are rejected.

- [x] **Step 2: Verify expected red**
  - Run: `python -m unittest discover -s tests -p "test_rea_engine_accounting.py" -v`
  - Expected: import failure because the new contract/ledger module is absent.

- [x] **Step 3: Implement minimum behavior**
  - Use `Decimal(str(value))`; avoid binary float accounting.
  - Track cash, inventory quantity, weighted average entry, realized PnL, total fees, and turnover.
  - Allocate entry fees proportionally to inventory sold; do not subtract costs twice.

- [x] **Step 4: Verify focused green**
  - Run the same unittest command.
  - Expected: all accounting tests pass.

- [x] **Step 5: Integration check**
  - Run `python -m py_compile research/rea_engines/contracts.py research/rea_engines/accounting.py tests/test_rea_engine_accounting.py`.
  - Expected: exit code 0.

### Task 2: Deterministic event replay and pending order lifecycle

**Files:**
- Create: `research/rea_engines/replay.py`
- Create: `tests/test_rea_engine_replay.py`

**Interfaces:**
- `ReplayConfig(starting_cash, max_inventory, max_fill_qty, costs, default_order_ttl=None)`
- `Replay.run(candles, strategy, config) -> RunReport`
- `RunReport` includes fills, rejections, pending orders, equity curve, drawdown, returns, exposure, costs, input digest, and safety flags.

- [x] **Step 1: Add focused failing tests**
  - Market intents fill only at the next candle open, with fee, half-spread and slippage separately recorded.
  - Resting buy/sell limits fill only on later candles when their range touches the limit; gap-through fills at a better open.
  - Limits persist, expire only when configured, and remain pending at end of data.
  - Duplicate/out-of-order timestamps, malformed OHLC, and empty input fail closed.
  - Two identical runs produce the same canonical metrics digest.

- [x] **Step 2: Verify expected red**
  - Run: `python -m unittest discover -s tests -p "test_rea_engine_replay.py" -v`.
  - Expected: missing replay import.

- [x] **Step 3: Implement minimum behavior**
  - Process only previously submitted orders before presenting the closed candle to the strategy.
  - Enforce stable submission order, cash/inventory/fill caps, explicit rejection reasons, and no same-candle fills.
  - Never infer queue position or intrabar path from OHLCV; record the fill-policy ID in reports.

- [x] **Step 4: Verify focused green**
  - Run the same unittest command.
  - Expected: all replay lifecycle tests pass.

- [x] **Step 5: Integration check**
  - Run Tasks 1 and 2 focused unittest patterns.
  - Expected: all focused tests pass.

### Task 3: Grid, DCA, and Combo clean-room strategies

**Files:**
- Create: `research/rea_engines/strategies.py`
- Create: `tests/test_rea_engine_strategies.py`

**Interfaces:**
- `Strategy.on_candle(candle, portfolio_snapshot, open_orders) -> list[OrderIntent]`
- `GridStrategy`, `DCAStrategy`, and `ComboStrategy` have frozen, explicit configurations.

- [x] **Step 1: Add focused failing tests**
  - Grid places one below-close limit while flat and a take-profit sell while holding.
  - DCA respects adverse-move steps, maximum add count, quote size, and take-profit.
  - Combo uses Grid entries while flat and DCA management while holding without conflicting intents.
  - Pending orders suppress duplicate submissions and all strategies remain deterministic.

- [x] **Step 2: Verify expected red**
  - Run: `python -m unittest discover -s tests -p "test_rea_engine_strategies.py" -v`.
  - Expected: missing strategy imports.

- [x] **Step 3: Implement minimum behavior**
  - Use only candle/portfolio/open-order data passed to the strategy.
  - No hidden tuning, network, exchange integration, or private-vendor behavior claims.

- [x] **Step 4: Verify focused green**
  - Run the same unittest command.
  - Expected: all strategy tests pass.

- [x] **Step 5: Integration check**
  - Run all `test_rea_engine_*.py` tests.
  - Expected: accounting, replay, and strategy tests pass.

### Task 4: Local CSV evaluation, benchmarks, cost stress, and provenance

**Files:**
- Create: `research/rea_engines/evaluation.py`
- Create: `research/rea_engines/cli.py`
- Create: `tests/test_rea_engine_evaluation.py`
- Create: `research/rea_engines/README.md`

**Interfaces:**
- `load_ohlcv_csv(path) -> (candles, source_sha256)`
- `evaluate_strategies(candles, configs, costs, source_sha256) -> dict`
- `canonical_report_sha256(report) -> str`
- CLI requires explicit local input/output paths and never downloads data.

- [x] **Step 1: Add focused failing tests**
  - CSV parsing preserves timestamps and verifies exact source SHA-256.
  - Missing/malformed files fail closed; no network fallback exists.
  - Fixed quote-allocation buy-and-hold benchmark is separated from fixed-quantity benchmark; deployed exposure and denominator are explicit.
  - 2x/3x cost stress increases or preserves total modeled costs.
  - JSON records dataset/config/cost/fill-policy hashes and always states `EDGE=UNPROVEN`, `network_calls=0`, `live_orders_submitted=0`.

- [x] **Step 2: Verify expected red**
  - Run: `python -m unittest discover -s tests -p "test_rea_engine_evaluation.py" -v`.
  - Expected: missing evaluation import.

- [x] **Step 3: Implement minimum behavior**
  - Require explicit file paths, canonical JSON, stable Decimal serialization, source and report digests, and no implicit I/O.
  - Evaluate fixed parameters on REA's OOS interval 2022-01-01T00:00:00Z through 2026-09-30T20:00:00Z only when the local CSV is found and its SHA-256 matches the recorded source.

- [x] **Step 4: Verify focused green**
  - Run the same unittest command.
  - Expected: all evaluation/provenance tests pass.

- [x] **Step 5: Final local verification**
  - Run: `python -m unittest discover -s tests -p "test_rea_engine_*.py" -v`.
  - Run `python -m compileall -q research/rea_engines tests/test_rea_engine_*.py`.
  - Run a source scan showing the new package has no network/exchange imports and no runtime imports.
  - Expected: all focused tests pass and compileall exits 0. Run the full SENEX suite only if a local pytest-capable interpreter is available; do not install from network.

- [x] **Step 6: Record evidence**
  - Write a local report only if the canonical data file exists and matches SHA-256 `7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146`.
  - Otherwise report the missing-data blocker and do not substitute synthetic data as market evidence.
  - Record exact commands, exit codes, test counts, report hash, and remaining limitations in `progress.md`.

## Unresolved product decisions

None required for the isolated first delivery. Any future runtime integration, public endpoint, deployment, order routing, or live-mode promotion is explicitly out of scope and requires a separate owner authorization.
