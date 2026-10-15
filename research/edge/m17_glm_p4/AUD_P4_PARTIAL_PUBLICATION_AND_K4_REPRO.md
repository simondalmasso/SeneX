# Independent AUD — GLM M17 P4 partial publication and K4 reproduction
Date: 2026-10-09 · [Order #208](https://github.com/simondalmasso/SeneX/issues/208) · [P3 #206](https://github.com/simondalmasso/SeneX/issues/206)
Publication: [DRAFT PR #210](https://github.com/simondalmasso/SeneX/pull/210).

## Byte-custody evidence
Owner uploaded 7 exact files from the originally claimed 9-file P4 package (6 SHA256-manifest entries + original `SHA256SUMS`):
- `00_SOURCE_RULE_ORACLE_ATTESTATION_MATRIX.csv`: SHA256 `e0e69d78ab25ff7ccab783609210f61953a6a25b2e7f20c11c381f7143e222ae`
- `03_ARQ_NON_DUPLICATIVE_RED_AND_STAT_LABELS.md`: SHA256 `a9ea56f2281ce7a0217dc67508bcd69b3ef318a4379ec7726036a1b5126489f3`
- `04_FINAL_STOP_OR_CONTINUE_VERDICT.md`: SHA256 `8b9de332225a44e866bdc908f6a57744e08f7a3470968a7f92ea87855b3e144b`
- `SOURCES_VERIFIED.json`: SHA256 `77cc03b7b575e621b482c033e4b6fbf1a212b980b566448c24015e8740c92f00`
- `k4_edge_costes.py`: SHA256 `3d906ded7674026e7e5f42708bdc916685df7d73a35ae73884a4a679e739e933`
- `k4_reproduction_log.txt`: SHA256 `560047a12ef622eac809c88b219ead3d2ef3cfb1cf94c5bf695392bf02791833`
- `SHA256SUMS`: original manifest, unchanged.

All seven GitHub file blobs match the original uploads. The P4 manifest still lists **two MISSING files** not attached to this conversation:
`01_HISTORICAL_REPLAY_FEASIBILITY.md` SHA256 `905daf1d8d01b49c0fdb99b9b189d400c0b29a3f3f53ff01b41224a71a9c4443`;
`02_NET_EDGE_IDENTIFIABILITY_DAG.md` SHA256 `7d2bc10e1aedbbcc138bb3e97138b39cfea548ffe7886b1e848c787ecd3b0728`.
Thus `P4_MANIFEST_CHECK=INCOMPLETE` **even though all attached bytes passed**. Do not fabricate either missing document.

## Independent K4 replay
Executed the owner's original `k4_edge_costes.py` logic in a clean local temporary directory without external network/provider/wallet calls, changing **only the hardcoded output directory in the disposable execution copy** to a temporary path; original uploaded script was not modified or overwritten. Environment local Python/NumPy with fixed seed `2026100903`, `OPENBLAS_NUM_THREADS=1`.

```text
SOURCE_SHA256=3d906ded7674026e7e5f42708bdc916685df7d73a35ae73884a4a679e739e933
EXECUTION_EXIT=0
EXPECTED_CSV_SHA256=c00fce39abef9600efeb4a38742e2a9fd5998a559a6203f45c2d173fdf5687a2
REPRODUCED_CSV_SHA256=c00fce39abef9600efeb4a38742e2a9fd5998a559a6203f45c2d173fdf5687a2
CSV_BYTE_EXACT=true
DATA_ROWS=35
FIELD_WIDTH=9
STDERR=EMPTY
```

The original **CRLF** `k4_resultados.csv` is preserved at `research/edge/m17_glm_p4/aud_k4/k4_resultados_original.csv` with Git blob `670d1c9e2556ae44a620016e2570156c0334544e` and original SHA256 `c00fce39...`. This CSV belongs to **P3**, not P4 original manifest.

## Scope of this verification
`K4=SYNTHETIC_NUMERIC_REPRODUCED`. Reproduction does not validate the scenario's market-realistic fee schedule, maker rebate assumptions, observed fill probabilities, exchange executable edge or a genuine BTC5m historical dataset.

P4's `HISTORICAL_SETTLEMENT_REPLAY=NOT_IDENTIFIABLE` means unavailable *original TWAP inputs* for the specified replay, **not** that payout labels cannot be independently witnessed at Polygon ConditionalTokens resolution; see [ARENA research #212](https://github.com/simondalmasso/SeneX/pull/212), currently without original chain JSON. The existence of a terminal on-chain label cannot attest the signed Chainlink TWAP calculation.

RED-06 refusal to substitute source, RED-07 no evidence-class promotion and RED-08 offline authorization guard may be useful as **fixtures**; a single total evidence-class order must not confuse orthogonal dimensions of source authority, time causality and execution evidence. No new data collection or authorization follows from P4.

```text
GLM_EXTERNAL_GITHUB_WRITE=BLOCKED
AUD_BRIDGE_PUBLICATION=7_OF_9_ORIGINAL_P4_FILES
K4=INDEPENDENT_SYNTHETIC_NUMERIC_REPRODUCED
P4_INDEPENDENT_SOURCE_VERIFICATION=PARTIAL
SOURCE_ADMISSIBLE=NO
COHORT_AUTHORIZED=NO
EDGE=UNPROVEN
ZERO_SPEND=HARD
PAPER_ONLY=true
NO_MERGE=true
NO_DEPLOY=true
```
