# GPTrader evidence packet — ORDER085

Source design export SHA256: `6f2840da23b62717bc2f9f4f887712e65b3e7e98bb2f6003d53f600243d87fd9`.

Measured repository facts used by the design:
- `oracle_runner` owns nominal 900s prediction cadence for BTC/ETH.
- Existing PAPER path is `PortfolioCoordinator -> RiskKernel -> ExecutionEngine -> TradeJournal -> ShadowLive`.
- `paper_view.py` is observational.
- `TradeJournal` defaults to local JSONL.
- public H011 runtime is read-only for mutation methods.
- PAPER/live safety locks are explicit.
- `_audit.decision_replay_v1` exists as a bounded T0 replay source.
- raw confidence/up_prob semantics are not established as calibrated probabilities.
- GPTrader MCP, journal and dashboard do not yet exist on main.
- ORDER085 performed no product implementation, deployment, D1 write, LIVE action or real order.

Owner corrections are recorded in `OWNER_REVIEW.md`.

This evidence packet is intentionally bounded; implementation must re-verify current main/runtime before ORDER086 mutations.
