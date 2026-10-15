# ARQ_M17_PROSPECTIVE_READINESS_HANDOFF

**PR:** [#204](https://github.com/simondalmasso/SeneX/pull/204), still DRAFT. **Base:** `583e11588cea8594db8625a01dc0305ec826f008`. See PR Conversation receipt for final HEAD and exact-head CI.

## Four independent readiness gates

- `ENGINEERING_READY=FIXTURE_OFFLINE_VERIFIED` **conditional on exact-head canonical CI**. Local new custody tests pass, hard Draft 2020-12 validator passes, and no product/runtime imports or network I/O exist in the new modules. This is not a prospective capture deployment or external custody proof.
- `SOURCE_ADMISSIBLE=NO`, `SOURCE_BLOCKED`. Public legacy `crypto_prices_twap_sixty` BTC/USD delivered one `subscribe` 60s snapshot and **zero forward `update` events** in the bounded 27.3-second F0 probe. Original snapshot bytes were retained separately on the authorized isolated owner host and SHA256 documented, but are **not** prospective original T0, signed reports or a verified event/receipt update chain. Modern authenticated stream remains unattempted without owner authorization. No generic `crypto_prices_chainlink` substitute.
- `PROSPECTIVE_COHORT_AUTHORIZED=NO`. Original clock uncertainty, independent market-rule authority, oracle provenance, public TWAP60 availability, off-host external immutability anchor, native SENEX 300s predictor, freeze of experiment dates and statistical thresholds, and AUD acceptance are missing.
- `ECONOMIC_EDGE_PROVEN=NO` — `N_REAL_VERIFIED=0` within M17 only, `EDGE=UNPROVEN`, `ABS_NET_OOS=NOT_COMPUTABLE`, `DELTA_VS_MARKET_NET_OOS=NOT_COMPUTABLE`.

## Delivered

`capture_offline.py` models T0 slots, late/lost-window records, raw-source provenance, per-token books and fee snapshots, exact signal binding and T1 candidate. `custody_store.py` preserves original synthetic fixture bytes on append-only journal with per-source SHA256 and restart verification. Neither module makes network requests or can upgrade a label to independently signed.

`strict_schema_gate.py` requires `jsonschema==4.26.0` Draft202012Validator (installed test-only, not in product runtime), validates both actual schemas, rejects eight mutation cases; if missing it **fails exit 2** without permissive fallback. Regression explicitly tests float leakage and rejects partial quote execution. `M17_TEST_MANIFEST.json` lists RED/GREEN and test commands.

`F3_PREREGISTRATION_DRAFT.json` defines hypothesis, full-window denominator including abstentions, train/calibration/OOS time blocks, paired net benchmark and uncertainty, multiple testing and stop conditions. All owner-dependent dates, effective N, clock tolerances, fee authority and external source gates are explicitly `null`: it is a design **not a frozen study**.

`GLM_PAPER_INTAKE_SCHEMA_V1.json` and template reserve independent review for new hypotheses, economic mechanism, measurable variable, leakage risk, falsification and versioned protocol; no auto-admission of the 15 papers or retrospective protocol mutation. AutoPilotPM comparison is reference only.

## Hard blockers before AUD promotion

1. Obtain a true, original, time-bound, independently credible BTC/USD 60s TWAP `update` source — not `subscribe` or generic relay.
2. Independently validate market-specific original rule/settlement and T0/T1 byte provenance, actual fee schedule and deterministic clocks.
3. Prove native SENEX BTC5m pre-decision predictions, instead of relabeling 1h/15m values.
4. Set independent external custody anchor (local hashes alone are alterable by store owner) and validate gap/restart behavior in an authorized prospective acquisition environment.
5. Freeze universe, start/end, hypothesis allocation, temporal splits, N, uncertainty, stop and AUD approval **before** first scientific T0.
6. Establish real executable fill evidence if economic edge is to be claimed. Synthetic quotes/fees never prove fills.

`ZERO_SPEND=HARD | PAPER_ONLY=true | LIVE=false | REAL_ORDERS=0 | CAPITAL=0 | NO_MERGE | NO_DEPLOY | EDGE=UNPROVEN`.

## Append-only ARQ1 P1 correction appendix — 2026-10-09

Independent AUD verdict [#6077235696](https://github.com/simondalmasso/SeneX/pull/204#issuecomment-6077235696) found three adversarial fixture-only classification errors despite the earlier full CI success. ARQ1 implemented the fixes without touching production: semantic cross-binding of original market rule/metadata bytes and any declared original rule SHA256; finite, nonempty, noncrossed Decimal bid/ask levels with explicit hypothetical candidate-share depth; exact wire millisecond parser with rejection of fractional float/truncation and booleans. False/missing associations add `NO_MARKET_RULE` or `NO_BOOK` plus `ABSTAIN`; timestamps that silently lose precision are rejected. Source admissibility and eligibility stay false.

TDD RED: 5 actual custody-backed assertions fail against the AUD-reviewed version (exit 1), followed by 2 additional failures for rule hash lineage and explicit desired shares (exit 1). TDD GREEN on repaired sources: 24 new test cases + 18 previous oracle tests, exit 0, none skipped when pinned real schema dependency is loaded. An explicit canonical CI step now installs `jsonschema==4.26.0` only into temporary test scope and runs the hard Draft202012Validator plus both focused suites with `set -euo pipefail`; original workflow steps and product lock unchanged. The per-commit canonical CI outcome is tracked separately in the final PR conversation receipt; this appendix is a historical design summary, not a self-issued AUD approval.

Internal fixture JSON keys, candidate shares and rule semantics are NOT verified external provider contracts. No proprietary original source update, real 5m score, external anchor, T1 authority, market fill, OOS edge, science cohort, or merge authorization has been added.
