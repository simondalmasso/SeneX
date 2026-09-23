# ARQ2-SCORE-READBACK-004

Status: **COMPLETE**
Verdict: **EXISTING_AUDIT_SUFFICIENT**
Interpretation: **DIAGNOSTIC_ONLY**. This order does not establish EDGE.

## Transport and bounds

Only the transport changed from ORDER-003. The readback used the existing Cloudflare/Wrangler authorization context and the remote D1 HOT/COLD bindings with SELECT statements only.

- HOT numeric-PK keyset: `id DESC`, page size 80.
- Maximum reached: 8 HOT pages / 640 HOT rows.
- No OFFSET, COUNT(*), admin scan, gateway secret access, H011 proxy, or deploy.
- COLD was queried only for exact BTCUSDT settled IDs selected from HOT.
- Every Wrangler result reported `rows_written=0` and `changed_db=false`.
- Raw page bytes remain only in the ephemeral execution workspace; repository evidence stores SHA-256 hashes.

The post-c7 cut uses the reference commit timestamp `2026-09-22T11:37:34Z`. All 44 selected rows independently proved exact runtime provenance with `source_commit=c7f7dca0...`.

## Phase 1 — completeness

640 HOT rows were examined. The bounded window contained 44 post-c7 BTCUSDT WIN/LOSS rows, so `PROBE_POPULATION_LT80=YES`. COLD returned 44/44 exact rows. HOT↔COLD id, audit digest, payload link and raw payload SHA-256 all matched.

All 44 rows contained full audit, decision replay, step2 `total_pressure` + `up_prob`, decision waterfall, 1h settlement price, exact c7 runtime provenance, proof-qualified origin/settlement evidence, and settlement observation provenance.

**Fully joinable: 44/44 = 100%. Completeness PASS (threshold 95%).**

## Phase 2 — discrimination

The frozen CORE selector `authoritative_score.independent_1h_cohort` reduced the 44 homogeneous rows to 15 non-overlapping 1h observations. Ties: 0.

- ROC-AUC raw `up_prob`: **0.3148148148**
- bootstrap 95% CI: **[0.0383547009, 0.6590909091]**
- Q1..Q5 future-UP frequencies: **[0.6667, 0.6667, 1.0000, 0.3333, 0.3333]**
- Q5−Q1: **−0.3333333333**
- bootstrap 95% CI: **[−1.0, 0.3333333333]**
- raw-pressure directional correctness: **5/15 = 33.33%**
- declared-direction correctness: **5/15 = 33.33%**

The bootstrap used 5,000 deterministic replicates with seed 20260923. These are retrospective small-N diagnostics with wide uncertainty. No calibration, tuning, feature selection, prospective claim, cost/execution claim, or EDGE claim is permitted.

## Waterfall / attrition

Within this settled post-c7 diagnostic sample, `decision_waterfall_v1.category=DIRECTIONAL_EXECUTE` for 44/44. Persisted final predictions: LONG=27, SHORT=17, FLAT=0. Parameterized raw reasons are preserved in the JSON summary.

PortfolioEngine/Meta/Kelly/Risk attribution remains **UNRESOLVED** because this readback does not directly prove downstream attribution.

## Decision

Existing persistence is sufficient to recover score, exact runtime provenance, proof-qualified 1h settlement and oracle waterfall above the 95% completeness gate. No new score ledger is needed or authorized by this order.

EDGE=UNPROVEN. No CORE mutation, HYP-008 mutation, deployment, LIVE action, real order, or capital action occurred.
