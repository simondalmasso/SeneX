# Oracle-aligned BTC5m net EV (M17)

Isolated paper research per [Issue #201](https://github.com/simondalmasso/SeneX/issues/201). **No live trading, no product modification, no real orders.**

The `oracle_custody` module can reject mismatched raw hashes, tokens and 5m windows, but cannot independently certify the authenticity of Chainlink oracle reports; it always returns `label_authority=UNVERIFIED`. `book_costs` models ask-side quote depth and market-specified fee curves with a conservative quote-only state. It never simulates actual order submission.

The F0 source registry records **reachability only**; captured hash digests without the full original source bytes are not source custody. Do not reuse F0 smoke timestamps or market metadata as historical T0. The scientific verdict remains `EDGE=UNPROVEN`.

See `EXPERIMENT_PROTOCOL.md`, schemas, `DATA_SOURCE_REGISTRY.json`, and `ORDER_M17_RECEIPT.json`.
