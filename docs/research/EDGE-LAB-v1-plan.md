# SENEX EDGE LAB v1 — Implementation Plan

> Research-only branch. SENEX CORE is read-only. No merge, deploy, LIVE, mainnet, capital, signing, broker connectivity, or runtime imports.

**Goal:** Build a minimal falsification harness that records immutable experiments and refuses invalid SENEX-vs-market comparisons.

**Base:** `cleanup/dependency-prune-v1@f58e2f3e5555b954a93b988225036b65b60ac9b8`

**Spec:** GitLab issue #6 plus the ARQ2 EDGE LAB order dated 2026-09-17.

**Known blocker discovered in Phase 0:** the persisted Polymarket prior is BTC Up/Down 5m, while SENEX proof-qualified outcomes are 15m/1h with 1h authoritative. Therefore current HYP-001 cannot claim a proper-scoring comparison until horizons and p_senex probability semantics are aligned.

## Task 1 — Fail-closed baseline contract
- Add `edge_lab/baseline.py`.
- Refuse Brier/LogLoss when horizons differ, outcomes are unresolved, or p_senex probability semantics are unvalidated.
- Unit test each refusal and a valid synthetic same-horizon path.

## Task 2 — Experiment contract and immutable ledger
- Add `edge_lab/contracts.py`, `experiment_ledger.py`, and `replay.py`.
- Persist JSONL experiment records with deterministic canonical/config hashes.
- Preserve RETAIN/REJECT/INCONCLUSIVE and negative-result memory.
- Detect exact semantic duplicates before rerun.

## Task 3 — Read-only adapters
- Add `edge_lab/adapters/senex_readonly.py` for persisted SENEX rows only.
- Add `edge_lab/adapters/polymarket_v2.py` enforcing GET-only requests to `https://data-api.polymarket.com/v2`.
- No auth, secrets, POST/DELETE/order endpoints, or production imports.

## Task 4 — Register queue and run only HYP-001 feasibility
- Register HYP-001 through HYP-007 in the research ledger.
- Run HYP-001 as a contract/feasibility evaluation only.
- Expected current verdict: INCONCLUSIVE if 5m-vs-1h and unvalidated p_senex semantics remain.

## Task 5 — Verification
- Focused EDGE LAB tests.
- Full SENEX pytest.
- PAPER-focused tests.
- compileall, diff-check, secret scan.
- Recheck PAPER lock invariants.
- Linux CI only if a safe research-only CI path exists; otherwise report CI=NOT_RUN rather than fabricate green.
