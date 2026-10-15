# Shared event/state contract — isolated implementation v1 (2026-10-14)

**Status:** implemented and tested in the isolated clean-room lab; NOT APPROVED for production or runtime integration. **Mode:** paper-only; **EDGE:** UNPROVEN. Audit labels and approval-reference fields in this helper are not an authorization or security mechanism.

## 1. Purpose and boundary

This document records the implemented paper-only event journal and reducer wrapper in `research/rea_engines/paper_execution_contract.py`. It defines an isolated contract for testing lifecycle, persistence, recovery, risk guards and reconciliation. It is not an integration approval, production migration or evidence of production behavior. No runtime module imports, production interface changes, order routing, credentials, venue access, deployment, or live/testnet actions are permitted under this proposal.

`Replay.run` remains a deterministic candle-based simulator. `PaperState` remains a reducer over caller-supplied fill events. Their accounting parity can be tested on equivalent inputs, but their order lifecycles and execution-price models are not interchangeable.

## 2. Existing facts to preserve

- `PaperState` schema v1 persists source/config SHA-256, cash, inventory, all-in inventory basis, realized PnL, cost totals, turnover, last processed timestamp, fill-event sequence, orders, processed fill IDs and their fingerprints.
- In the existing v1 helper, `last_event_seq` is effectively a **fill-event sequence**, not a sequence for every order lifecycle event. A future full event stream must not silently reinterpret this field; it would require an explicit schema/version migration.
- `apply_fill` consumes incremental fill quantity and per-event absolute fee/spread/slippage costs. An exact duplicate fill ID plus matching fingerprint is a no-op; a conflicting payload under the same ID fails closed.
- `Replay` computes simulated fills from later OHLCV candles; normal replay fills are whole-pending-order fills and do not model queue position or probabilistic partial execution. Its limit-cost assumptions differ from market-cost assumptions.
- Replay's accounting `Ledger` enforces `max_inventory` and `max_fill_qty`; the standalone resilience helper does not hold these two limits. It cannot replace RiskKernel or ExecutionEngine controls.

## 3. Implemented journal and checkpoint schemas

Each journal line is canonical JSON with exactly these top-level fields: `journal_seq`, `event_id`, `event_type`, `timestamp`, `payload`, `prev_sha256`, and `event_sha256`. `event_sha256` hashes the canonical record excluding itself and includes the previous record hash. Records are UTF-8, newline-delimited, use sorted keys and compact JSON; quantities and money are decimal strings. The journal format is versioned by the module contract; the checkpoint envelope carries `schema_version=1`. Source/config hashes live in the `PaperState` and checkpoint, while risk bounds are serialized separately and must exactly match on recovery.

Implemented event types and required payload fields:

| Event | Payload fields | Contract |
|---|---|---|
| `ORDER_REGISTERED` | `order_id`, `client_order_id`, `side`, `quantity` | Stable order/client IDs; conflicting reuse fails closed. |
| `FILL_APPLIED` | `fill_id`, `order_id`, `side`, incremental `quantity`, `price`, absolute `fee`, `spread_cost`, `slippage_cost`, normalized `timestamp`, `fill_seq` | Fill sequence is distinct from journal sequence. One fill may be applied only once; conflicting fill IDs fail closed. |
| `ORDER_CANCELLED` / `ORDER_EXPIRED` | `order_id`, `remaining_qty`, non-empty `reason` | Terminal lifecycle event; preserves already-filled quantity and has zero accounting effect. |
| `SNAPSHOT_RECONCILED` | `cash`, `inventory_qty`, `open_orders`, `last_event_seq`, `tolerance` | Stores observed snapshot and discrepancies; a mismatch sets a persistent block. |
| `DIVERGENCE_RESOLVED` | `approval_ref`, `reason`, `action`, `prior_discrepancies` | Only action is `retain_local_paper_state`; records an explicit paper-only review decision and does not import/overwrite the observed state. The reference is audit metadata, not authenticated approval. |

Order cancellation and expiry statuses are persisted in the wrapper checkpoint overlay (`terminal_statuses`) because the pre-existing `PaperState` v1 order model has no cancel/expiry status. Consumers must ask `PaperExecutionEngine.status(order_id)`, not infer terminal lifecycle from the nested v1 `PaperOrder.status`. The wrapper rejects later fills for canceled/expired orders. Expiry is emitted by the caller's TTL/bar-clock policy; the wrapper does not independently calculate elapsed bars.

Checkpoint envelope fields are exactly `schema_version`, `state_sha256`, and `engine_state`. The checkpoint includes the `PaperState` payload, terminal status overlay, journal sequence/head, last lifecycle timestamp, block reason/discrepancies, and exact risk limits. Saving uses a temp file, flush/fsync and atomic replacement. The checkpoint path must differ from the journal path.



## 4. State transition and idempotency rules

1. Validate identifiers, event schema, timestamp, numeric fields, fill sequence, order state and risk limits before appending. `PaperState.last_event_seq` is a **fill-only sequence**; `journal_seq` increments for registrations, fills, cancel/expiry, reconciliation and resolution.
2. Exact `event_id` plus matching type/timestamp/payload is a no-op, even if later journal entries exist. Reusing an event ID with different content fails. A repeated `fill_id` with identical fill inputs is a no-op; changed inputs under that fill ID fail closed.
3. New events have nondecreasing normalized UTC timestamps. A new fill sequence must be exactly `PaperState.last_event_seq + 1`. Incremental fill quantity cannot exceed the order remainder, per-fill `max_fill_qty`, or resulting `max_inventory`; existing reducer guards also prevent overspend and short inventory.
4. Cancel/expire applies only to an open or partially-filled order and captures its exact current remainder and a reason. It does not affect cash, inventory, basis, costs, turnover or PnL; after the terminal event, additional fills are rejected.
5. Before append, the candidate transition is reduced in memory for validation. The journal record is appended using binary mode, serialized under an OS writer lock, checked against the current byte offset to reject stale writers, then flushed with `fsync`. Only after successful append/fsync is in-memory state advanced. A write/fsync/close uncertainty freezes that instance. A partial/truncated tail fails recovery closed; it is not silently discarded.
6. Recovery checks every record's schema, sequence, canonical hash and predecessor hash. It rebuilds state from genesis through the checkpoint sequence and requires an exact state/overlay/head match to the checkpoint, then replays every later record. It does not skip directly to a tail or overwrite local state from an external snapshot.
7. Reconciliation compares cash, inventory, fill sequence, open-order IDs, side and remainder. Any discrepancy is durably journaled and blocks further mutations. Resumption requires an explicit `DIVERGENCE_RESOLVED` audit event with a non-empty reference and reason; it retains local paper state and does not establish that any external account is reconciled. The supplied reference is not authenticated by this module.
8. One writer per journal path is enforced during appends using an OS lock and stale-size check. This module remains a paper-only local harness; the lock, hash chain and string approval reference are not a substitute for production-grade access controls, storage guarantees, external journal ownership or a security review.



## 5. Accounting invariants

For each buy, cash decreases by `quantity × price + fee + spread_cost + slippage_cost`; the same total is added to inventory cost basis. For each sell, cash increases by gross notional less its costs; inventory basis is allocated pro rata, with the exact remaining basis allocated when inventory is fully closed. Realized PnL for a sale is net proceeds less allocated basis. Fees, spread, slippage and turnover are accumulated exactly once. With a consistent mark, `realized_pnl + unrealized_pnl = equity - starting_cash` within the Decimal arithmetic model.

A parity test compares `Ledger` and `PaperState` only when both are given identical fill quantities, prices and absolute cost inputs. It does not assert that Replay's OHLCV-generated fills match external execution fills, or that their cost assumptions represent a live venue.

## 6. Authority and integration gates

If a separately approved adapter is ever proposed, ExecutionEngine remains the order/fill source of truth, RiskKernel retains veto/kill-switch authority, TradeJournal preserves the required audit semantics, and venue precision/min-notional/filter checks must be explicit. No adapter may bypass existing risk controls or turn model-generated replay fills into claims of external fills. Replay and resilience remain decoupled until there is a versioned mapping for IDs, order lifecycle, fills, costs, sequence ordering, cancellations, recovery and failure policy.

Required gates before any runtime proposal: (a) reviewed ownership/license/data-use scope; (b) approved event and state schema with a migration story; (c) documented risk/journal ownership; (d) cross-module tests listed below; (e) restart/replay and reconciliation proof; (f) separate security and architecture review; (g) explicit approval. This isolated implementation and its tests satisfy only the local paper/research behavior gate; they satisfy none of the licensing, production security, architecture approval or runtime integration gates by themselves.

## 7. Verification matrix

| Case | Required assertion | Current status |
|---|---|---|
| Same buy/partial-buy/sell/partial-sell stream fed to both reducers | cash, inventory, basis, realized PnL, fees, spread, slippage and turnover match after every fill | `tests/test_rea_engine_accounting_resilience_parity.py`; 2 tests pass |
| Exact duplicate fill | no second booking; parity remains intact | parity suite plus wrapper/reducer tests pass |
| Same fill ID, different payload | fail closed; original state unchanged | existing resilience test passes |
| Sequence gap, stale unseen event, backward timestamp | fail closed; original state unchanged | reducer sequence-gap test and wrapper backwards-timestamp test pass |
| Overfill, unknown order, side mismatch, overspend, attempted short | fail closed; original state unchanged | existing reducer tests plus wrapper overspend/short/no-mutation tests pass |
| Partial fill, checkpoint/restart, duplicate-after-restart, next event | no lost/double fill; sequence and remaining quantity continue | existing reducer restart test and wrapper post-checkpoint replay/checkpoint-prefix tests pass |
| Snapshot matches/mismatches partial state | match is clean; mismatch lists cash/inventory/sequence/order discrepancies and blocks mutations without overwriting local state | reducer snapshot tests and wrapper multi-field divergence/review tests pass |
| `max_inventory` and `max_fill_qty` policy | per-fill cap, post-buy inventory cap, cash/no-short guards; no accounting commit on rejection | implemented and tested in isolated wrapper |
| Replay vs resilience fill/cost model differences | differences are explicit; no inferred queue/partial-fill behavior | separate models remain; no market execution equivalence claimed |
| Cancel/expiry lifecycle | terminal status, exact remainder, no accounting side effect, rejection of later fill, idempotent event retry | implemented and tested |
| Journal durability/writer ownership | append-first state transition, fsync, canonical hash chain, no truncated-tail recovery, stale writer rejection | implemented and tested in local file-based harness; not production journal proof |
| Checkpoint + later events | validate source/config/risk/hash and replayed prefix; reconstruct all events after checkpoint in sequence | implemented and tested |
| Divergence and explicit resolution | divergence record blocks mutations; resolution records reason/reference and retains local state only; no silent external overwrite | implemented and tested; reference is not authenticated |
| Venue filters, precision, minimum notional, retry/clock drift | invalid input or unresolved external execution state fails closed | missing; must remain an integration blocker |


## 8. Current decision

`PARTIAL_RESEARCH_COMPLETE`; `PAPER_ONLY`; `EDGE=UNPROVEN`; product/runtime integration **NOT APPROVED**. The cross-module parity test demonstrates matching accounting only for identical synthetic fill inputs. The wrapper tests demonstrate local paper lifecycle, journal integrity and recovery behavior; they do not validate market edge, commercial bot behavior, external venue execution, or production readiness. Commercial executable analysis remains paused pending explicit authorization and scope.

## 9. Implementation closure — 2026-10-14

The five requested paper-only contract gaps are implemented in `paper_execution_contract.py`, without modifying `execution_resilience.py` or any `senecio_polymarket/backend` file. The wrapper is not imported by runtime.

- **Cancel/expiry:** journaled terminal state overlay, exact current remainder and reason; zero accounting effect; post-terminal fills reject; exact retry is idempotent. TTL expiry remains emitted by the caller's clock/bar policy.
- **Journal durability:** canonical JSONL hash chain, append under OS writer lock, binary append mode, `fsync` before in-memory state update, stale-writer size check, and fail-closed behavior for uncertain/partial writes.
- **Recovery:** validates all event schemas, sequence numbers, hashes and links; rebuilds from genesis through checkpoint and verifies exact checkpoint state; replays all later events in order. Rejects corrupted/truncated journal, source/config or risk-limit mismatch, and mismatched checkpoint prefix.
- **Risk:** per-fill `max_fill_qty`, resulting `max_inventory`, insufficient cash and attempted short are rejected before journal commit.
- **Reconciliation:** compares cash, inventory, fill sequence and open-order ID/side/remainder. Discrepancy is recorded and blocks mutation. Resolution records a reference/reason and retains local state only; it does not claim external state is reconciled. The reference is not authenticated.

Focused verification for this module: `tests/test_paper_execution_contract.py` — **22 passed, 1 existing pytest-asyncio deprecation warning**. Final verification: 71 REA tests passed; the eight-file REA/dashboard bundle passed 80 tests; 652 repository tests passed when excluding the one file that cannot be collected without `sse_starlette`; targeted `compileall` and four frontend JavaScript syntax checks passed. The unfiltered collection still reports one missing-dependency error.

**Decision remains:** `PARTIAL_RESEARCH_COMPLETE`; `PAPER_ONLY`; `EDGE=UNPROVEN`; product/runtime integration **NOT APPROVED**. Production-grade journal ownership, authenticated approval, venue filters/precision/min-notional, external retry/clock-drift behavior, and separate security/architecture review remain out of scope and blocked.

## 10. Latest closure validation — 2026-10-14

Verification time (local): 2026-10-14T20:39:04.499612-03:00.995773-03:00.

- research/rea_engines/paper_execution_contract.py — SHA-256 3f6eb48a93a7d6e00ff8cf4c1d79ac6e621d745fa85d261c0bc45445235e301b; the digest matches the verified file.
- tests/test_paper_execution_contract.py — SHA-256 cb7f07d39f27949cde7d29839004e92716b4e33043da18d142975befcf928dfb; the digest matches the verified file.
- Dedicated contract tests: **22 passed, 1 deprecation warning**.
- Eight-file focused bundle across REA, contract and dashboard tests: **80 passed, 1 warning**.
- Repository suite excluding only tests/test_health_readiness.py: **652 passed, 92 warnings**.
- The unfiltered repository suite remains **not verified**: collection exits 2 because sse_starlette is absent. No dependency was installed.
- Both static dashboard JavaScript files pass Node syntax validation.
- SHA-256 checks for all five protected runtime files match the recorded values. No runtime interface was edited in this continuation.

This closes the isolated paper-contract implementation and focused-test evidence, not the full product/repository approval process. TTL scheduling remains owned by the caller; approval_ref is audit metadata, not authenticated authorization; a retain-local-state decision is not evidence that an external venue/account state has been reconciled. State remains PARTIAL_RESEARCH_COMPLETE, PAPER_ONLY, EDGE=UNPROVEN; runtime integration is NOT APPROVED.

## 9. Binance public-data diagnostic bridge — local and read-only

Verification timestamp (local): 2026-10-14T21:00:56.253909-03:00.

The local SENEX FastAPI app now registers a limited public-data router with two manual GET paths: /api/binance/public_klines and /api/binance/rea_paper_diagnostic. The first uses fixed public Binance Spot Klines and server-time endpoints only; the second converts verified closed bars to the REA Candle contract and invokes clean-room Grid/DCA/Combo replay plus benchmarks. The route has an operator environment switch SENEX_BINANCE_PUBLIC_DATA_ENABLED=0, an allowlist for BTCUSDT/ETHUSDT and 15m/1h/4h/1d, maximum dashboard limit 100, memory caching and rate limiting.

The API router and container recipe package only deterministic contracts/accounting/replay/strategies/evaluation modules. The durable paper_execution_contract wrapper remains isolated and is not wired to the production order source, risk veto, or journal. No credentials, account endpoints, signed requests, order/cancel requests, real/testnet orders or deployment are part of this stage. A local code-tree integration is not proof that the production service was deployed or that the UI has been visually verified. Status remains PAPER_ONLY; EDGE=UNPROVEN; live order integration not approved.

## 10. Post-integration regression result — 2026-10-14

Local verification time: 2026-10-14T21:06:30.838236-03:00. After the manual public Binance feed and paper diagnostic routes were wired into the local FastAPI source, the applicable repository suite passed **685 tests with 92 warnings** and exit code 0, excluding only tests/test_health_readiness.py. The unfiltered collection remains blocked by missing sse_starlette; no packages were installed.

The five execution/risk/journal interfaces remain hash-stable. The API path only reads public market candles and runs aggregate clean-room paper evaluation; it does not invoke ExecutionEngine, RiskKernel or TradeJournal and cannot submit orders. The durable paper-execution contract is still not wired into the runtime. PAPER_ONLY; EDGE=UNPROVEN.

## 11. Binance public sample — 1,000 closed bars

A 1,000-closed-bar public-data snapshot was fetched on 2026-10-14 using Binance Spot public GET endpoints only. Window: 2026-05-01 00:00 UTC through 2026-10-14 12:00 UTC; zero gaps, no interpolated candles, no incomplete candle in the returned 1,000 rows.

- Raw Klines response SHA-256: 1a6b578307b867cf2816990276cf876218c1914ee75a0c993a823548e84bf56b.
- Canonical closed-candle SHA-256: 23b2e9d9cdf5b903dfd48c34a1cc7c62f4ebabb00e34c16193eb9ef748ffabc3.
- Grid: +0.0090% net / +0.90 USDT; 2 fills; max drawdown about 0.168%; 3x cost stress about +0.0070%.
- Combo: +0.0575% net / +5.75 USDT; 4 fills; max drawdown about 0.266%; 3x cost stress about +0.0535%.
- DCA: +0.1918% net / +19.18 USDT; 17 fills; max drawdown about 0.553%; market exposure about 99.3%; 3x cost stress about +0.1478%.
- Fixed-quantity buy-and-hold benchmark: +0.8751% / +87.51 USDT; max drawdown about 4.78%. Quote-allocation buy-and-hold: about +0.0567% / +5.67 USDT.

These are fixed-parameter clean-room simulations on one recent market window with hypothetical 10,000 USDT, inventory/fill caps of 0.02, and assumed costs of fee 10 bps, spread 5 bps and slippage 5 bps. DCA was nearly fully exposed and materially lagged fixed-quantity buy-and-hold; Grid and Combo traded very little. This does not establish a durable edge or a trade recommendation. EDGE remains UNPROVEN, and trading is not enabled.
