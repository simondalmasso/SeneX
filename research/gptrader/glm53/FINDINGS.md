# ORDER087 — GLM5.3 Findings

STATUS=P0_REVIEW_ACTIVE
TARGET_ORDER086_SHA=de495bccb9676c425aa4522a2423dfdbf4a32059

Only evidence-backed findings belong here. Findings below were reconciled against the pushed ORDER086 P0 commit and the ORDER085 design contract.

## B1 — Torn JSONL line can permanently halt future GPTrader sealing

Classification: CONFIRMED_BUG

Evidence:
- ORDER086 commit: `de495bccb9676c425aa4522a2423dfdbf4a32059`
- file/symbol: `senecio_polymarket/backend/gptrader/sealer.py::PacketSealer._scan`
- file/symbol: `senecio_polymarket/backend/gptrader/sealer.py::PacketSealer.seal`
- integration: `senecio_polymarket/backend/oracle_runner.py`
- observed behavior from code: any non-empty malformed JSON line raises `PacketSequenceError`; every later `seal()` calls `_scan()` again, so the same malformed line causes repeated failure.
- integration behavior: oracle_runner catches the sealing exception, logs a warning, and continues the SENEX prediction cycle.

Expected behavior:
- append-only durability must fail closed without silently disabling all future GPTrader packet collection;
- a torn final write must have an explicit recovery/quarantine policy or otherwise prevent permanent collection outage.

Impact:
- SENEX remains alive while GPTrader silently accumulates a permanent packet gap;
- scientific sample completeness can diverge from the control stream without a hard readiness failure.

Recommended disposition:
- forward-fix P0 without rewriting history;
- add a crash/torn-tail recovery rule that is deterministic and auditable;
- expose sealer health/gap state;
- add regression fixture that injects a torn final JSONL line, restarts, and proves the chosen recovery/fail-closed behavior.

## B2 — P0 violates ORDER085 packet-size hard bound

Classification: CONFIRMED_BUG

Evidence:
- ORDER085 design contract: packet target <=4 KiB; hard bounded projection <=8 KiB.
- ORDER085 CALL_BUDGET: if oversized, remove nonessential detail and retain identity/hash/decision features.
- ORDER086 commit: `de495bccb9676c425aa4522a2423dfdbf4a32059`
- file/symbol: `senecio_polymarket/backend/gptrader/sealer.py::build_sealed_packet`
- file/symbol: `senecio_polymarket/backend/gptrader/sealer.py::PacketSealer.seal`
- observed behavior from code: no serialized-byte-length check or deterministic compaction path exists before append/serve.
- GLM5.3 reported an external probe on real-shaped records of approximately 21.7 KiB median. That measurement is reviewer-reported and should be reproduced in repo tests before being used as canonical numeric evidence; the contract violation exists independently because no <=8 KiB bound is enforced.

Expected behavior:
- sealed decision projection <=8 KiB hard bound;
- target <=4 KiB;
- deterministic removal of nonessential broad replay/market detail when necessary.

Impact:
- Decision MCP payload/call budget can be exceeded;
- broad nested replay fields enlarge attack surface and scheduler/token cost;
- batch size 16 can become much larger than designed.

Recommended disposition:
- forward-fix P0;
- enforce UTF-8 canonical serialized size <=8192 bytes;
- deterministic compaction/truncation policy preserving packet identity inputs required by the scientific protocol;
- add real-shaped max-size fixture and a boundary test.

## B3 — Missing failure-mode fixtures for append/recovery/oversize behavior

Classification: TEST_GAP

Evidence:
- P0 test file covers projection, stable hash/id, resolved-outcome refusal, restart/idempotent sequence, and integration ordering.
- no fixture covers torn JSONL tail;
- no fixture covers checkpoint/log crash interleavings;
- no fixture covers packet-size hard bound;
- no fixture asserts collection-health visibility after sealing failure.

Recommended disposition:
- add these tests with B1/B2 fixes before Decision MCP is considered ready.

## B4 — Exact-name contamination refusal remains a latent schema-evolution risk

Classification: TEST_GAP

Evidence:
- contamination detection is recursive but keyed to the finite `OUTCOME_FUTURE_KEYS` set.
- current known settlement fields such as `outcome_15m`, `outcome_1h`, `outcomes_dual`, `price_1h_later`, `settled_at`, settlement proof/CAS fields are covered by the current denylist or excluded by the allowlist.
- future schema aliases could escape if added inside an allowed nested structure without updating the denylist.

Recommended disposition:
- keep allowlist narrow;
- add a contract test generated from known settlement schema keys;
- fail CI if a settlement/future key is introduced into an allowed decision-time projection without explicit review.

## D4 — Primary evidence counting unit must remain pinned in implementation

Classification: DESIGN_GAP / IMPLEMENTATION_GUARDRAIL

Evidence:
- ORDER085 requires non-overlapping primary 1h units and same-hour BTC/ETH clustering.
- ORDER085 strong gate requires >=600 resolved independent 1h units AND >=14 calendar days.
- P1 has not yet been pushed, so implementation compliance is not yet evidenced.

Required implementation:
- overlapping 15m packets MUST NOT increment independent N;
- BTC/ETH observations in the same hour share a cluster id and are not treated as IID duplicates;
- primary uncertainty must be dependence-aware;
- raw row count must be reported separately from independent-unit count.

Disposition:
- must be proven by P1 tests before P1 is accepted.

## P0 non-issues / positive evidence

Classification: NON_ISSUE

At target SHA:
- fresh predictor rows use `outcome=None` / later-price placeholders and are sealable;
- projection is allowlisted and recursively strips known future/outcome keys;
- resolved outcome contamination is refused;
- packet hash/id are stable over semantic ordering;
- restart/idempotent sequence behavior has a targeted test;
- seal integration occurs after local prediction persistence and before remote mirror/settlement;
- GPTrader remains additive and PAPER-only;
- no broker/wallet/signer/LIVE path was introduced by P0.

## Artifact import note

The files uploaded from the GLM5.3 sandbox in the owner chat are seed patches for the initial ORDER087 workspace, not the later local commits containing the expanded ~110-case matrix and complete P0 review.

Reviewer reported local commits:
- `3003b53c`
- `787e600f`

Those local commit objects are not present in GitHub and should not be treated as durable evidence until their content is imported or reconstructed here.

Do not mark B1/B2 FIXED until a fresh ORDER086 commit plus regression tests/readback proves the correction.
