# SENEX integration delta — read-only architecture map

Audit date: 2026-10-14. This is design only. The five runtime files below were read and hashed; none was modified. The isolated package under research/rea_engines is not imported by backend runtime. No live mode, API, exchange adapter, scheduler or trading lock was changed.

## Existing SENEX interfaces observed

| File | Observed API | Integration implication | Current boundary |
|---|---|---|---|
| senecio_polymarket/backend/portfolio/execution_engine.py | ExecutionEngine: submit, _try_fill, _try_fill_high_fidelity, cancel, cancel_replace, _open_or_add_position, set_stop_target, check_exits, get_open_positions, get_recent_exits, get_audit_log, equity, stats, update_config, enable_live_mode; models Order, Fill, Position | A future adapter may translate a validated intent into existing order contracts and reconcile fills/audit events | No connection; no live-mode call |
| senecio_polymarket/backend/portfolio/risk_kernel.py | RiskKernel: init_state, evaluate, record_pnl, update_vol_regime, trip_kill_switch, reset_kill_switch, get_state, update_config; RiskDecision, KernelState, VolRegime | Risk decision must veto/reduce intent before execution; kill switch and daily limits remain authoritative | Not imported or modified |
| senecio_polymarket/backend/portfolio/trade_journal.py | TradeJournal: on_audit_event, open/exit handlers, _append, optional _mirror_to_supabase, query/statistics methods | Future bridge should consume canonical execution audit events, not create a competing ledger | No journal writes or Supabase access |
| senecio_polymarket/backend/execution_simulator.py | ExecutionSimulator: execute, monitor_exits, risk_state; fee/depth helpers | Useful signal-to-position simulator, but not equivalent to historical event replay with resting limits | Keep fill models distinct |
| senecio_polymarket/backend/binance_sim_lane.py | BinanceSimLane: _load_if_present, _persist, _open, _close, on_prediction, public_state, public_trades | Candidate simulation consumer only after schema/ownership review and restart tests | No invocation or state mutation |

## Interface SHA-256 at audit time

- execution_engine.py: 3ad44c2474047aac110e71a9924276ac7cb391a83e565454b885754bf50d7523
- risk_kernel.py: 9e8291655795051a1296db7475044bda67775d974cee2c8aad24ebcf80cf49c5
- trade_journal.py: 12f81c94b8db44a8a247b308d8de910ca61771720240ad2c7daf31bfd10041f2
- execution_simulator.py: 3381a6ecfa888ddde6c56e5fcee2b228e22fb6225704e060da84fd3440192291
- binance_sim_lane.py: 4a799b61fbf00868e7636a204ccdabda0868a391b871e6b8620f67af0545aa0f

## Clean-room package flow

Local CSV with pinned SHA-256 -> Candle validation -> Strategy.on_candle -> OrderIntent/CancelIntent -> deterministic replay -> pending-order/fill lifecycle -> Decimal ledger -> equity/drawdown/cost metrics -> canonical JSON report.

- contracts.py: typed candles, snapshots, intents, fills/rejections and cost profile.
- strategies.py: independent spot-only Grid, DCA and Combo with explicit state.
- replay.py: validated OHLCV chronology; process prior orders; market fills at next open; resting limit fills only when a later OHLC range touches; cancellation/expiry/rejection and equity curve.
- accounting.py: cash-backed inventory, average cost basis, realized/unrealized PnL and cost attribution.
- evaluation.py: exact input SHA, fixed OOS window, annual independent slices, benchmarks and 1x/2x/3x cost stress.
- cli.py: local CSV only; no download fallback, exchange SDK, credentials or order router.

## Planned adapter — not implemented

1. Define a separately reviewed typed adapter that maps OrderIntent to the existing paper-only order schema. Reject unsupported types rather than silently translating them.
2. Call RiskKernel before every intent; risk veto, caps, kill switch and daily limits take precedence.
3. Treat ExecutionEngine fill/reject/cancel events as the single execution source of truth. Reconcile partial fills, pending orders, inventory and average cost before another strategy action.
4. Persist strategy state with schema version, source/config hashes, last candle timestamp, pending order IDs and last reconciled event sequence. Recovery must be idempotent and fail closed on missing/corrupt state or hash mismatch.
5. Emit one canonical journal event per state transition; avoid double-counting costs or PnL between replay and runtime.
6. Start with deterministic contract tests and shadow-only dry runs in a separate harness. Require explicit approval before any runtime import.

## Differences and risks

Historical replay uses completed OHLCV and deterministic fills; SENEX execution classes have different order/fill abstractions and may model depth/stops. The lab is spot-only, long-only and cash-backed; it does not model venue precision/filters, minimum notional, queue priority, partial-fill probability, rate limits, disconnect retries, clock skew or manual/external fills. BinanceSimLane is not authorization to use Binance testnet/live. TradeJournal's optional Supabase path must not be triggered. Do not import bytecode-derived commercial pseudocode into this clean-room package.


## Research-only resilience module (implemented, not wired to Replay or runtime)

execution_resilience.py supplies long-bracket OHLC ambiguity policy (conservative stop-first, optimistic target-first, or reject ambiguous), sequenced partial-fill accounting, immutable paper-state checkpoint serialization with SHA-256 and atomic replacement, source/config identity validation on load, and non-mutating snapshot reconciliation. Duplicate fill IDs are idempotent only when the full event fingerprint matches; reuse with a different payload fails closed.

It is exercised by tests/test_rea_engine_resilience.py. It has no network/exchange adapter, is not called from Replay.run, is not imported by production runtime, and does not change the historical BTC report.
