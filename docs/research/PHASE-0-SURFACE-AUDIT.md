# EDGE LAB v1 — Phase 0 research-surface audit

Baseline: `f58e2f3e5555b954a93b988225036b65b60ac9b8`

## AVAILABLE
- Prediction output and persisted audit.
- `decision_replay_v1`.
- Decision-time Polymarket snapshot with `condition_id`.
- `pipeline.step2_features.up_prob` (probability semantics explicitly unvalidated).
- Proof-qualified 15m/1h settlement evidence; 1h is the authoritative SENEX horizon.
- PAPER execution, TradeJournal, forensics, simulated execution fidelity fields.
- Existing public/read-only Polymarket CLOB and Kalshi adapters.

## MISSING
- A Polymarket market-prior observation aligned to SENEX's authoritative 1h resolution semantics, or a SENEX proof-qualified 5m outcome contract.
- Validation/calibration contract making `p_senex` eligible as a probability for Brier/LogLoss/ECE.
- Decision-time observed Polymarket execution costs aligned to the evaluated trade.
- Prior EDGE LAB ledger/replay infrastructure (added by this branch after this audit).

## AMBIGUOUS
- Brier/ECE on current `p_senex`.
- Economic edge where simulated and observed cost fields would be mixed.
- Cross-venue comparisons without exact contract/resolution equivalence.

## STALE / EXCLUDED
- Observation-time Polymarket fallback used as if it were a historical decision-time prior.
- Any market snapshot whose freshness/resolution provenance cannot be established.

## HYP-001 feasibility result
Current persisted evidence contract is not score-comparable:
- Polymarket snapshot: BTC Up/Down 5m.
- SENEX authoritative settlement: 1h.
- `p_senex`: `UNVALIDATED_MODEL_UP_PROB`.

Verdict: `INCONCLUSIVE`.
No Brier, LogLoss, ECE, directional edge, economic edge, or promotion claim is emitted.
