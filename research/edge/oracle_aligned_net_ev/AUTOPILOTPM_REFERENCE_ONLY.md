# AutoPilotPM reference only — M17 P0

Inspected the upstream `recogardtech/AutoPilotPM` file `src/feeds/polymarket/rtds.ts` at pinned commit `f3612cc3188802d722bc047d037abb69fabf97e8`, read-only. It uses WebSocket connect, ping and retry/reconnect timing; subscribes to `crypto_prices` and `crypto_prices_chainlink` but **not** BTC/USD `crypto_prices_twap_sixty`; parsed messages are emitted after JSON decoding without append-only preservation of the original frame bytes. Neither source origin, clock bounds, signed Chainlink attestation nor scientific gap accounting is established by that feed.

M17 reimplements the useful *concepts only*: explicit `RECONNECT` and `GAP` original-byte records, full SHA256, no continuity claim without a known previous sequence, and hard-fail restart verification. No runtime, dependency, WebSocket client implementation, wallet, signing, trade features or paper/live toggles imported or installed. Upstream software is not evidence of profitable edge.
