# ORDER #197 — Candle Economic Challenger V1

Standalone, **RESEARCH/PAPER-only** BTCUSDT 1h economic falsification experiment. Contains NO imports from production decision/sizing paths; uses NumPy and pytest; does not update ORDER100 or the frozen first Challenger Lab cohort.

**Running:** `py -3 -m pytest -q tests/test_candle_edge_*.py`. Library entry points: `contracts.parse_snapshot(snapshot)`, `candle_features.extract_features(hourly)`, `candle_features.classify_patterns(hourly)`, `economic_labels.economic_trade(...)`, `walkforward.evaluate_abcd(rows)`.

This **is not** a live feed nor a trading recommendation. No historical BTCUSDT original T0 dataset has been retrieved into this isolated checkout. No "real" OOS result, PnL, or incremental edge can therefore be computed: `BLOCKED_ARTIFACT_BYTES`.

T0 schema expects an attested original prediction ID, decision timestamp (UTC), exchange/spot-or-perpetual market type, source SHA256, and exactly 16 consecutively CLOSED 15m UTC bars from the saved decision-time `decision_replay_v1.market.ohlcv`. The sha256 supplied by a caller is only a claimed custody hash: **hash format is not proof of prior append time or immutable source custody**. Full scientific admission requires separately verified original raw bytes and timestamped lineage receipts; never backfill from present-day OHLCV.

No 1h bar may end later than T0; no incomplete 15m bars, venue switches, missing candles, ambiguous timestamps, conflicting close times or invalid OHLC. Output contains four aggregated closed 1h candles. Four hours support the fixed minimum features/pattern fixtures, not a 55-pattern search or calibrated win probabilities.

The geometrical pattern rules are minimalist **V1 research definitions** and not bit-equivalent to TA-Lib (which has adaptive candle settings/longer lookbacks). A setup-only Hikkake uses three already closed bars; a later confirmation is NEVER available to a preceding decision. Zero-range or incomplete history raises `EvidenceError` (UNKNOWN, not false). A separate licensed/pinned offline TA-Lib equivalence exercise would be needed before claiming equivalence.

For actual evaluation, the caller must independently align one frozen T0 row and a genuine existing 3600s reference label by exact identity, verify source/label custody outside this module and use only past labels in training folds. `evaluate_abcd` accepts **prevalidated** aligned rows; in-memory inputs are never proof of externally verifiable source custody. `custody_verified` must not be set without independent evidence. If bytes unavailable return `BLOCKED_ARTIFACT_BYTES`; the function itself handles empty input.

A = unchanged historical SENEX side. B = SENEX side with train-only fitted geometry abstention; C = train-only pattern abstention; D = both. No candidate reverses direction. Same OOS test indices, target, fixed notional, cost model, and FLAT opportunities. All three fit L2=10 with train-only scaling, no post-test retune. Candidate threshold predicted train-fit net bps > 0 is frozen.

**Cost stress:** 10/15/20/30bps round trip; 15bps scenario = fee 10bps + slippage 5bps proxy, not venue-verified execution. True spreads, latency, funding and fill quality remain unverified. No ATR hypothetical payoff; 1h reference TIME_STOP only. The reference return is not an executed fill.

**Uncertainty:** expanding purged walk-forward with mandatory 3600s embargo beyond 1h label end, 24-observation circular moving-block bootstrap, frozen seed 7, primary paired net-bps difference per opportunity and conservative 3-way multiplicity correction. Minimum 300 independent OOS opportunities; >=100 executed actions per candidate when evaluating promotion; positive 15/20/30bps net and CI lower >0 needed even for `HISTORICAL_OOS_CANDIDATE_ONLY`. Real economic promotion still requires a distinct future PAPER prospective preregistration. This module cannot authorize LIVE.

**Hard gates:** `ZERO_SPEND=HARD; PAPER_ONLY=true; LIVE=false; REAL_ORDERS=0; CAPITAL=0; EDGE=UNPROVEN`.

See `EXPERIMENT_PROTOCOL.md` and `FINAL_RECOMMENDATION.md` for exact epistemic status.
