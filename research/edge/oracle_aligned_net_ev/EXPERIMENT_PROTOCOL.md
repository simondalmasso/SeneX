# SENEX M17 — F0 inventory and prospective research protocol

**Authority:** [Issue #201](https://github.com/simondalmasso/SeneX/issues/201) and its [addendum](https://github.com/simondalmasso/SeneX/issues/201#issuecomment-6066285490). Base pinned: `583e11588cea8594db8625a01dc0305ec826f008`. Branch: `research/m17-oracle-aligned-net-edge`.

## Safety and isolation

`PAPER_ONLY=true; LIVE=false; REAL_ORDERS=0; REAL_CAPITAL=0; ZERO_SPEND=HARD; NO_MERGE; NO_DEPLOY`.
This lane is research-only and creates no production endpoints, order API calls, account mutations, signing, wallets, or changes to ORDER100, ORDER197, H011, or UI.

## F0 verified at 2026-10-08 21:45 UTC (bounded read-only)

- Existing `senecio_polymarket/backend/polymarket_market_adapter.py`: discovers Gamma BTC 5m windows, normalizes event/market/condition/UP+DOWN token IDs, GETs CLOB books, maintains WS BBO/depth and freshness. It does **not** independently seal original T0 raw market/source snapshots for M17.
- A fresh GET returned active `btc-updown-5m-1791495900`, `market_id=5416655`, `condition_id=0xa2c7ab20b13fb54b31fdf1e6a1dc0d495a6aabfe7f6d4128db2381bcb15fa7f4`. Gamma response byte count 6873; SHA256 `1dc64ddeb55661983a628a20aa7e32f45fb40e3c3859efc4163e0ce6c358d6a0`.
- Fee inspection for first token: public `GET /fee-rate` HTTP 200, `base_fee=1000`; contemporaneous Gamma `feeType=crypto_fees_v2`, `feeSchedule={exponent:1,rate:0.07,takerOnly:true,rebateRate:0.2}`, `cryptoMarketConfig.id=btc-5m-twap-60`. This is **this market only**. Fee response SHA256 `72f0f488905219fa9b2f2c5374dfbf9e83345b1fa38826015b15119ac221a079`.
- One public RTDS WebSocket subscription to `crypto_prices_chainlink` filtered `btc/usd` succeeded and delivered one `update` with payload fields `full_accuracy_value,symbol,timestamp,value`. Raw frame SHA256 `976ec0a238ca1b130b50775968b596480ba54da39ef4bae0045d59864f521ba8`. It is a **provider-declared relay**, not a signed Chainlink Data Streams report.

**Critical limit:** the original bytes from these ephemeral F0 probes were not durably archived in the repo or a T0 store. Their hashes **alone do not prove original data custody**, cannot be treated as T0 or T1, and must not enter any confirmatory sample. No outcome inspection, retrospective reconstruction, claimed fills, or profit analysis was performed.

## Hard blocks before F4/F5

1. An independently verified exact original market rule, tie/boundary timestamps, oracle lineage and 5m tokens.
2. Immutable forward-only original T0 bytes + timestamp/provenance + market-specific fee and two independent books, before outcome.
3. Independent T1 original bytes, final Polymarket settlement **and** authoritative Chainlink verification. Binance is proxy only; relay alone is not signed attestation.
4. Actual existing 5m SENEX prediction availability at T0; the existing 15m runtime schedule cannot be fabricated as 5m features.
5. Frozen train-only calibration / A_NO_TRADE-B_MARKET_PRIOR-C_SENEX_CALIBRATED same-index policies with abstention=0.
6. Prospective untouched cohort, preregistered effective N/stopping, dependence-aware paired inference, stress, multiplicity correction, and AUD.

## Phase gates

- **F0:** source inventory + bounded smoke only. **Observed:** APIs reachable, but original custody missing.
- **F1:** `oracle_custody.py` validates hashes/identity/time and tie-rule fixture. Its result is `MATCHED_BYTES_ONLY`; **never** automatically promotes to attested oracle labels.
- **F2:** `book_costs.py` performs fee/ask-depth/PAPER quote accounting; paired UP/DOWN upper bound only. No actual fill evidence.
- **F3:** frozen train-only probability calibration and comparators. **NOT STARTED** — no admissible prospective T0/label provenance.
- **F4:** append-only writer and asynchronous join. **BLOCKED** pending original market/oracle/feed capture contract and zero-spend custody.
- **F5:** OOS report. **BLOCKED** until preregistered prospective cohort, fills and independent labels.
- **F6:** optional challengers **NOT AUTHORIZED**.

## Tests

`python -m unittest discover -s tests -p test_oracle_net_contract.py -v` — 8 synthetic RED→GREEN contracts. The tests do not establish market-specific fees or an observed edge.

Scientific outcome: `BLOCKED_ARTIFACT_BYTES`, `LABEL_UNVERIFIED`, `PAPER_FILL_PROXY`, `EDGE=UNPROVEN`. All prospective economic quantities remain `NOT_COMPUTABLE` until custody and sample gates pass.

## AUD P1 correction protocol — 2026-10-09 (research-only)

AUD [comment #6075341020](https://github.com/simondalmasso/SeneX/pull/204#issuecomment-6075341020), owner [fix request #6075438614](https://github.com/simondalmasso/SeneX/pull/204#issuecomment-6075438614).

- T0 canonical names: `token_id_yes/no`; T1: `exact_oracle_source`. Both schema fixture definitions are exercised in `tests/test_oracle_net_p1.py`. The receipt byte input `t0_receipt_raw` is **required** and is not silently reconstructed by the production linker. `T0_receipt_sha256` commits to those bytes. The receipt must parse to the passed T0 object; original T0/T1 source, original rule and rule-version hashes must match.
- The local rule-version policy for F1 is the exact SHA256 of `market_rule_raw` on both ledgers. This **does not** authenticate the actual market/oracle rule without an external independently verified original source. Tie handling must be `UP_ON_EQUAL` for the fixture; no universal source assumption.
- Missing model signal is `prediction_id=null, frozen_senex_score=null, score_provenance=NO_T0_SENEX_SIGNAL, side_candidate=ABSTAIN`; missing book and fee inputs similarly null/empty and flagged. `classify_opportunity` accounts for an exclusion as ONE original opportunity with 0 strategy PnL; there is still no actual M17 writer or cohort.
- Fee arithmetic uses `Decimal` and a 5-place minimum quantum. Official source documents five-place rounding but does **not** independently establish the tie-breaking rule in our captured evidence; ROUND_HALF_UP is an explicit synthetic convention, **not** settled executable fee custody.
- The market sampled in F0 was marked `btc-5m-twap-60` and **cannot** use generic `crypto_prices_chainlink` as exact 60s TWAP. Modern Secure Realtime authenticated `prices.crypto.twap` produces decimal strings (`windowSeconds=60`, `btcusd`); not authorized for uncredentialed ZERO_SPEND use. Legacy uncredentialed RTDS `crypto_prices_twap_sixty` returned **only a subscribe snapshot** with `window_s`, not an actual forward update; E18 update parsing is a **synthetic contract only**. Distinguish both from signed Chainlink reports. See `TWAP_SOURCE_PROTOCOL_DECISION.json`.
- The separate AUD patch branch `research/aud-m17-contract-fix-20261009` was examined **without** cherry-pick, merge or simultaneous mutation. All repairs are authored in ARQ branch only.
- Continue with F3/F4/F5 **only** after original prospective T0/T1 source custody, exact market oracle/feed authority, frozen protocol, separately evidenced fills and independent AUD review. Current `EDGE=UNPROVEN`.
