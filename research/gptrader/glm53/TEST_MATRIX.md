# ORDER087 — Adversarial Test Matrix

## A. Sealed T0 packet integrity

- [ ] Nested outcome/future fields are recursively removed.
- [ ] A source record already containing resolved outcome is refused.
- [ ] Canonical serialization produces stable SHA256.
- [ ] packet_id remains stable across restart.
- [ ] Settlement cannot mutate a previously sealed packet.
- [ ] Error/log paths cannot leak outcome or later price into Decision MCP.

## B. Cursor and idempotency

- [ ] Duplicate identical submit creates exactly one decision.
- [ ] Duplicate identical submit creates at most one simulated fill.
- [ ] Conflicting retry fails closed.
- [ ] Stale cursor causes zero mutations.
- [ ] Cursor survives process restart.
- [ ] Two concurrent submits cannot double-fill.
- [ ] Cursor cannot advance beyond decisions not durably committed.
- [ ] Partial batch failure does not silently skip packets.

## C. Replay ordering

- [ ] Immutable decision is durable before future evidence is read.
- [ ] Already-known future outcome is inaccessible during decision generation.
- [ ] Settlement can append result but not edit decision.
- [ ] Re-running replay is deterministic for same packet/decision.

## D. Scientific validity

- [ ] raw up_prob/confidence is never treated as calibrated probability.
- [ ] Strong verdict blocked below 600 independent 1h units.
- [ ] Strong verdict blocked below 14 calendar days.
- [ ] BTC/ETH same-hour observations are not counted as independent IID samples.
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

Populate evidence columns only after real ORDER086 commits exist.
