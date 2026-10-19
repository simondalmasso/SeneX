# Oracle-aligned BTC5m net EV (M17)

Isolated paper research per [Issue #201](https://github.com/simondalmasso/SeneX/issues/201). **No live trading, no product modification, no real orders.**

The `oracle_custody` module can reject mismatched raw hashes, tokens and 5m windows, but cannot independently certify the authenticity of Chainlink oracle reports; it always returns `label_authority=UNVERIFIED`. `book_costs` models ask-side quote depth and market-specified fee curves with a conservative quote-only state. It never simulates actual order submission.

The F0 source registry records **reachability only**; captured hash digests without the full original source bytes are not source custody. Do not reuse F0 smoke timestamps or market metadata as historical T0. The scientific verdict remains `EDGE=UNPROVEN`.

See `EXPERIMENT_PROTOCOL.md`, schemas, `DATA_SOURCE_REGISTRY.json`, and `ORDER_M17_RECEIPT.json`.

## AUD P1 repair boundary

`validate_receipt_linkage(..., t0_receipt_raw=original_bytes)` requires the separately archived exact original T0 receipt bytes to bind T1's `T0_receipt_sha256`, along with original raw T0, T1 and market-rule bytes. Reconstructed canonical receipt bytes are **synthetic test fixtures only** and never independent source custody. All outcomes remain `LABEL_AUTHORITY=UNVERIFIED` regardless of hashes. Exclusions including `NO_T0_SENEX_SIGNAL`, `NO_BOOK`, `UNVERIFIED_FEE` preserve denominator slots.

The public `crypto_prices_chainlink` topic is not the BTC5m-specific 60s TWAP. Legacy `crypto_prices_twap_sixty` and modern authenticated `prices.crypto.twap` have separate parsers, source clocks and eligibility gates; see `TWAP_SOURCE_PROTOCOL_DECISION.json`. No in-code network connections are created.
