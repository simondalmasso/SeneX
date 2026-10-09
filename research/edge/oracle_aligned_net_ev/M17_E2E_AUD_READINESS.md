# SENEX M17 — ORDER #213 E2E AUD readiness handoff
**Engineering only; not scientific promotion.** Date 2026-10-09.

## Source lineage and exact hashes
- Protected `main`: `583e11588cea8594db8625a01dc0305ec826f008`.
- Existing P1 Draft [#204](https://github.com/simondalmasso/SeneX/pull/204): `ffd87cb20e4e83564a15d57b7c18b9cceab1ae3a`. No modifications, verified earlier canonical CI [37905534019](https://github.com/simondalmasso/SeneX/actions/runs/37905534019). Fixture-only.
- P2 Draft [#211](https://github.com/simondalmasso/SeneX/pull/211): `6e5d480983b1236882567f11ae6892f3c865c2c0`; [CI 37928293718](https://github.com/simondalmasso/SeneX/actions/runs/37928293718) success, 600 passed/10 skipped, no original provider authority.
- UI Draft [#209](https://github.com/simondalmasso/SeneX/pull/209): `8342e8d9883c8f856cc465ce74c331f4f399acb3`; [CI 37938630720](https://github.com/simondalmasso/SeneX/actions/runs/37938630720) green, browser artifact [11619122614](https://github.com/simondalmasso/SeneX/actions/runs/37938630720/artifacts/11619122614). Independent 18 PNG / 9 geometry manifest review [ARQ UI209 receipt](https://github.com/simondalmasso/SeneX/pull/209#issuecomment-6082787369). 390 effective CSS width unverified (Chromium min500); 72-row fixture PASS, no client-side polling tested.
- GLM P4 Draft [#210](https://github.com/simondalmasso/SeneX/pull/210): nine original artifacts delivered, original SHA256 manifest 8/8 PASS per independent AUD, K4 synthetic reproduction only. Separate stacked CI-fix Draft [#214](https://github.com/simondalmasso/SeneX/pull/214) classifies only original CRLF CSV as binary under `.gitattributes`, preserves bytes, tests other code whitespace detection; deployment prohibited.
- ARENA [#212](https://github.com/simondalmasso/SeneX/pull/212): five report bytes exactly archived; original `evidence/*.json`, `SOURCES_VERIFIED.json` and source SHA manifest NOT DELIVERED; purported specific on-chain tx and 96-event sample unverified.
- This new **isolated stacked E2E branch** descends from exact P1 HEAD; eleven P2 modules/docs/tests are referenced by their original Git SHA blob objects, unchanged. New E2E interface and negative fixtures are isolated from both original PRs.
- Prior P1 vs P2 interface analysis: [M17_E2E_COMPATIBILITY_MATRIX.md](M17_E2E_COMPATIBILITY_MATRIX.md). Source-specific guidance in [AUD #211](https://github.com/simondalmasso/SeneX/pull/211#issuecomment-6081458409).

## Reproduction and CTF documentary boundaries
- New `tests/test_m17_e2e_contracts.py`: 20 targeted synthetic negative/positive contract tests, real temporary P1/P2 custody and reopening. Initial RED on deliberately incomplete but importable module: 16 expectation FAIL, two behavioral ERR, two pre-existing invariants passed. GREEN: 20/20 PASS, including 1 original on-disk raw bridge and 1 documentary dual-attachment tamper/restart test.
- Existing P2 cases 27 PASS, existing P1 oracle contract 18 PASS, P1 prospective readiness 24 PASS; Draft202012 exact schema gate PASS (test-only pinned dependency), 2 positive/8 negative.
- `ConditionResolution` original on-chain receipt, chain finality, reporting oracle mapping, contract ABI decode and Gamma original market bytes are **not present in repo**. The "raw bytes" in synthetic fixtures are original **fixture bytes**, not original external provider messages. No RPC or API calls performed.
- The class `OfflineCompatibilityEvidence` extends only an offline allowlist of receipt kinds (`P2_COMPAT_FIXTURE`, `CTF_GAMMA_FIXTURE`), never `T0_SLOT`; its positive documentary match returns `SOURCE_ADMISSIBLE=NO`, `LABEL_AUTHORITY=UNVERIFIED`, `FILL_PROVEN=NO`.
- Valid payout vector ≠ signed Chainlink TWAP input. Causal T0, venue fills, independent rule authority, original 300s model signal, frozen trials and OOS net economics all HOLD.

## Rejected ARENA / GLM overclaims
`EV(B)≤0` does **not** imply `EV(C)≤0` without a proven strategy-class upper bound; `|accuracy_proxy − accuracy_official| ≤ label_disagreement_rate` is a rate statement, not a dollar profitability falsifier. Unregistered 20% boundary mass is **not** an admissible kill switch. GLM K6 maximum-statistic synthetic bootstrap is NOT fully implemented Hansen SPA nor DSR. Numerical `P(fill)=.44`, K3/K5/K6 FPRs, or 2.75¢ cannot become default risk parameters.

## AUD stop conditions
No parent merges, no production changes, wallet/RPC/authenticated feed, provider spend, retrospective-to-prospective promotion or M17 economic claims. The exact-head CI receipt and whether workflow activation was possible on the stacked PR appear in the new PR comment. Missing original ARENA JSON/manifest and post-audit source and cohort authority are hard STOP gates.
