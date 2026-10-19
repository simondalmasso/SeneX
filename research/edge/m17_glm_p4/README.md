# GLM research delivery inbox — M17 P4 (DRAFT / evidence only)

Canonical research order: https://github.com/simondalmasso/SeneX/issues/208
Previous hostile P3: https://github.com/simondalmasso/SeneX/issues/206
ARQ engineering order: https://github.com/simondalmasso/SeneX/issues/205

This branch is an **unmerged research evidence intake** for GLM-5.3. It was created to eliminate owner copy/paste between GLM's external sandbox and the SENEX repository. Its presence **does not grant an external agent credentials**: owner must connect GitHub to the GLM runtime once (prefer an authorized GitHub App; otherwise short-lived fine-grained PAT restricted to this repository, never pasted into chat, code, logs or issues).

## Publication contract
GLM should publish the **actual byte-for-byte original output files** from its P4 sandbox, maintaining paths under:

`research/edge/m17_glm_p4/M17_GLM_P4_DECIDABILITY/`

Expected files: `00_SOURCE_RULE_ORACLE_ATTESTATION_MATRIX.csv`, `01_HISTORICAL_REPLAY_FEASIBILITY.md`, `02_NET_EDGE_IDENTIFIABILITY_DAG.md`, `03_ARQ_NON_DUPLICATIVE_RED_AND_STAT_LABELS.md`, `04_FINAL_STOP_OR_CONTINUE_VERDICT.md`, `SOURCES_VERIFIED.json`, `SHA256SUMS`, and original K4 script/log **only if already present**.

Optional P3 archive location (separate and clearly synthetic): `research/edge/m17_glm_p3/`.

Required: one commit or small coherent commits on **this branch only**, fully qualified provenance (source URI, accessed status, whether raw original), original output SHA256 manifests, a script to validate file hashes + strict CSV row lengths, and reproducibility commands. If original outputs contain secrets, personal tokens, signed credentials, or paid/restricted source material, **do not commit them**: redact only derived publication copies and report withheld evidence in the PR without publishing secrets. Keep the actual originals in secure local custody.

## Never silently promote research to authority
- `SYNTHETIC_NUMERIC_REPRODUCED` is not real Polymarket fills, oracle attestation or net edge.
- No new provider calls/auth, no prospective capture/cohort, no wallets/orders, no spending, no new GitHub Actions secret consumption.
- GLM may update **only research reports/artifacts** on this branch, not M17 code, H011, ORDER100 or ORDER197.
- This branch's PR stays **DRAFT / UNMERGED** pending independent AUD and owner authorization. No deploy.
- Report any missing GitHub write permission as `GLM_GITHUB_WRITE_BLOCKED` rather than claiming submission.

```text
ORDER=M17-GLM-P4-SOURCE-DECIDABILITY
PUBLICATION_CHANNEL=research/glm-m17-p4-evidence-intake
RESEARCH_ONLY=true
PAPER_ONLY=true
ZERO_SPEND=HARD
LIVE=false
REAL_ORDERS=0
CAPITAL=0
SOURCE_ADMISSIBLE=NO
COHORT_AUTHORIZED=NO
EDGE=UNPROVEN
NO_MERGE=true
NO_DEPLOY=true
```
