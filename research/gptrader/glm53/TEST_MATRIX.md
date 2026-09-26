# ORDER087 — Adversarial Test Matrix

## A. Sealed T0 packet integrity

- [x] Nested known outcome/future fields are recursively removed in P0.
- [x] A source record already containing resolved outcome is refused in P0.
- [x] Canonical serialization produces stable SHA256 in P0 test.
- [x] packet_id remains stable across packet_seq differences in P0 test.
- [ ] Settlement cannot mutate a previously sealed packet after restart/replay.
- [ ] Error/log paths cannot leak outcome or later price into Decision MCP.
- [ ] KNOWN settlement schema aliases are exhaustively rejected from allowed nested structures.
- [ ] Future schema-key contract test fails if a new settlement field enters allowed projection.

## A2. P0 durability / crash consistency — merge gate

- [ ] Torn final JSONL line has deterministic documented recovery/fail-closed behavior.
- [ ] Torn final JSONL line cannot create permanent silent packet-collection outage.
- [ ] Sealer health exposes corruption/gap state.
- [ ] Crash after packet append but before packet_seq checkpoint is restart-safe.
- [ ] packet_seq checkpoint ahead of log fails closed with explicit health state.
- [ ] Two writers/processes cannot create duplicate seq or interleaved invalid JSONL records.

## A3. Packet-size budget — merge gate

- [ ] Canonical UTF-8 sealed packet <=8192 bytes hard bound.
- [ ] Normal packet target <=4096 bytes where practical.
- [ ] Oversized packet uses deterministic compaction of nonessential detail.
- [ ] Compaction never removes required identity/direction/score/provenance fields.
- [ ] Real-shaped fixture exercises broad decision_replay/external-market content.
- [ ] 8192-byte boundary behavior is tested.
- [ ] Batch of 16 remains bounded by MCP response budget.

## B. Cursor and idempotency

- [x] Cursor survives P0 process restart test.
- [ ] Duplicate identical submit creates exactly one decision.
- [ ] Duplicate identical submit creates at most one simulated fill.
- [ ] Conflicting retry fails closed.
- [ ] Stale cursor causes zero mutations.
- [ ] Two concurrent submits cannot double-fill.
- [ ] Cursor cannot advance beyond decisions not durably committed.
- [ ] Partial batch failure does not silently skip packets.
- [ ] Crash after durable decision before cursor write recovers idempotently.
- [ ] Crash before durable decision cannot advance cursor.

## C. Replay ordering

- [ ] Immutable decision is durable before future evidence is read.
- [ ] Already-known future outcome is inaccessible during decision generation.
- [ ] Settlement can append result but not edit decision.
- [ ] Re-running replay is deterministic for same packet/decision.

## D. Scientific validity

- [ ] raw up_prob/confidence is never treated as calibrated probability.
- [ ] Strong verdict blocked below 600 independent 1h units.
- [ ] Strong verdict blocked below 14 calendar days.
- [ ] 599 independent units fails gate.
- [ ] 13 calendar days fails gate.
- [ ] Overlapping 15m packets do not increment primary independent N.
- [ ] BTC/ETH same-hour observations share a cluster id.
- [ ] Raw resolved-row count reported separately from independent-unit count.
- [ ] Primary uncertainty uses temporal block/bootstrap or equivalent dependence-aware method.
- [ ] Wilson interval is descriptive only.
- [ ] ANTI_INFORMATIVE is distinct from NOT_USEFUL.
- [ ] Positive PnL alone cannot emit useful-signal verdict.
- [ ] Fee/slippage stress can force EXECUTION_ASSUMPTIONS_DOMINATE.
- [ ] GPTrader policy failure is distinguishable from SENEX signal failure.

## E. PAPER-only safety

- [ ] No broker client reachable from GPTrader package.
- [ ] No wallet/signer/private-key path.
- [ ] No LIVE action enum.
- [ ] No exchange order endpoint.
- [ ] Decision MCP cannot invoke arbitrary URL/shell/backend actions.
- [ ] Direct GPTrader D1 reads/writes remain zero.
- [ ] Public H011 mutation surface remains absent.
- [ ] SENEX native PAPER control journal is untouched.

## F. Privacy / owner-scale simulation

- [ ] Exact owner wallet amount is absent from public repo.
- [ ] Exact owner wallet amount is absent from public dashboard/logs.
- [ ] Optional owner-scale amount can come only from private config/env.
- [ ] Public metrics remain normalized/generic.
- [ ] No Binance account/wallet API is required.

## G. Operational resilience

- [ ] ChatGPT outage does not stop deterministic baselines.
- [ ] Backlog catch-up remains bounded.
- [ ] Missing Decision MCP results in WAIT/FAIL-CLOSED.
- [ ] Restart reconstructs state without duplicate fills.
- [ ] UNKNOWN stays UNKNOWN rather than silently zero.
- [ ] Sealer failure is observable and cannot masquerade as a healthy zero-volume period.

Evidence status is tied to exact ORDER086 commit SHA; re-review changed surfaces after every relevant forward fix.
