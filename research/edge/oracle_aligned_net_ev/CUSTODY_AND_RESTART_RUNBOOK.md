# M17 isolated evidence custody: fail-closed runbook

## Hard boundary

`capture_offline.OfflineCapture` and `custody_store.AppendOnlyEvidence` operate **fixtures/offline only**; no network transport, live recorder, orders, wallet, signing, paid API, deployment or real capital. Every receipt has `phase=FIXTURE_OFFLINE` and `source_admissible=false`. The record is a **local integrity check**, not an externally authenticated oracle or immutable remote timestamp.

Each append stores original bytes without transformation in a separate named file, using exclusive creation and fsync. It hashes raw bytes with SHA-256. One canonical `journal.jsonl` line records source clocks, market/window ID, flags, artifact paths, byte sizes/digests, monotonically increasing journal sequence and previous-entry SHA256 chain hash. Reopen verifies links, canonical bytes, blob digests, expected files, unique slots, T1 provenance ordering, torn tails and symlinks. A single-writer lock blocks concurrent writers; a leftover crash lock blocks restart until separate manual investigation.

A crash *after* original blob fsync but *before* journal fsync leaves an orphan. Restart refuses; **never** delete or auto-reconstruct its source. A truncated journal, removed or altered blob, duplicate slot or post-outcome T0 is also a hard stop. Original opportunities are either real-time T0 fixture events or irrecoverably `MISSED_WINDOW` slots recorded after end, with one denominator slot, abstention and zero hypothetical PnL.

T0 source slots distinguish independent `market_metadata`, `market_rule`, `book_yes`, `book_no`, `fee_yes`, `fee_no`, `senex_signal` and optional TWAP60 bytes. Source clock dictionaries record event and local receipt milliseconds and mandatory contiguous book sequence; missing first predecessor or evidence gaps → `GAP/ABSTAIN`. `NO_SIGNAL`, `NO_BOOK`, `NO_FEE`, `STALE`, `GAP` force `ABSTAIN`. The presence of a claim `horizon_s=300` is insufficient: the native score, model ID and event time must be bound to the original source bytes. No 1h/15m proxy.

Synthetic T1 bytes are stored only after end and only against an existing T0 slot; a platform claim, even marked `final`, is not independently signed. The T1 receipt always reports `label_authority=UNVERIFIED, n_real_verified=0`.

**Known limitation:** a local SHA256 chain does not defeat an attacker with complete control over the directory who rewrites both journal and blobs. Hardware/remote independent anchoring, authoritative source signatures, clock uncertainty and source admissibility are separate prerequisites. No external storage is provisioned under ZERO_SPEND.

## Recovery

Stop writing. Preserve every original byte, including orphans and damaged journal tails. Record the failed condition outside the mutable directory, provide an independent AUD read-only copy and refuse restart until adjudicated. Do not replay historical messages as forward data. No background watcher or T0 scheduler exists.

## Tests

`python -m unittest discover -s tests -p test_m17_prospective_readiness.py -v`: 15 synthetic test cases, including tamper, duplicate, orphan, torn tail, lost window, restart, wrong source score, discontinuous book sequence, precision and no-forward-fill.
