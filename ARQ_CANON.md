# ARQ_CANON.md

PROJECT=SENEX  
PURPOSE=Continue SENEX safely as the single implementation/execution ARQ after AUD authorization.  
REPO=https://github.com/simondalmasso/SeneX  
LIVE=https://h011-web--senecio-h011--wbjggn89fnf8.code.run/

## LAST_VERIFIED / BRANCH / HEAD
LAST_VERIFIED=ORDER084 canonical convergence phase
BRANCH=main  
ORDER084_START_MAIN=99ccac05a552d86d129a2675b6ab1c2604a2770f
DEPLOYED_CORE=c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a  
EDGE_011=9ad2c160f2ade19299a1d7dc2f0cfcbe6c13233c

## CANONICAL LINKS
- GitHub: https://github.com/simondalmasso/SeneX
- GitLab historical repo: https://gitlab.com/simondalmasso/SENEX
- Control Plane #7: https://gitlab.com/simondalmasso/SENEX/-/work_items/7
- Drive canon/evidence: https://drive.google.com/drive/folders/1yYryhMTzJhDxtxQyGMWasQLfY1P1awqg
- H011: https://h011-web--senecio-h011--wbjggn89fnf8.code.run/

## CURRENT STATE
- GitHub main contains the deployed CORE c7 lineage through ORDER084 history-preserving convergence plus canonical GitHub CI/canon docs.
- H011 currently reports exact CORE c7f7dca0..., READY, PAPER, orders disabled, live capital locked.
- `migration/gitlab-core-c7` preserves deployed CORE; `migration/gitlab-edge-011` preserves the frozen 011 research head.
- GitHub main is unprotected.
- Issue #68 is VOID/closed; ORDER084 Issue #69 is the current implementation authority.
- SOURCE-006 remains unresolved for fresh post-011 investigation; HYP008 remains PAUSE; Gate B/C are not accepted.
- 011: COLLECTING, PROGRESS_N=0, zero normal D1 reads before 2026-09-28T00:23:35Z.
- EDGE=UNPROVEN; LIVE=NO; REAL_ORDERS=0; CAPITAL=0.

## DONE
- GitHub mirror/cutover + CI authority.
- Post-cutover science/safety parity.
- c7 runtime repair and healthy PAPER runtime evidence.
- Bounded score readback proved existing audit persistence is sufficient.

## ACTIVE WORK
ORDER084_ACTIVE; ARQ1 is the sole writer/implementer/integrator/deployer.
H011 remains exact c7 during convergence/frozen 011; no deploy occurs in Phase 1/2.

## PENDING
- Canonical reconciliation: GitHub main vs deployed c7.
- Main protection after reconciliation.
- Frozen 011 execution only after its time gate.
- Re-evaluate SOURCE-006 and Gate B/C only if a new AUD order explicitly revives them.

## BLOCKERS / RISKS
- Deploying main now may downgrade/replace working c7 behavior.
- Stale historical orders conflict; current-state evidence wins.
- `up_prob` is not a calibrated probability.
- Early-look or extra D1 reads can invalidate 011.

## DO_NOT_TOUCH
- H011 deployment/config until explicitly ordered.
- `migration/gitlab-core-c7`, `migration/gitlab-edge-011`, archive refs.
- 011 manifest/progress/rows except under its frozen runner after the gate.
- HYP008/TOP1/TOP3/Agentics.
- wallets, private keys, LIVE, real orders, capital.
- legacy draft PRs #24/#25/#36.
- VOID Issue #68.

## AUTHORITIES / GATES
PAPER_ONLY=true  
ORDERS_ENABLED=false  
LIVE_CAPITAL_LOCKED=true  
EDGE=UNPROVEN  
READY_FOR_REARM=false  
011_GATE_NOT_BEFORE=2026-09-28T00:23:35Z

## WHERE_TO_RESUME
Read-only from three exact anchors:
1. GitHub `main@0cd8b6907f87e8fa178eb0c6c2c878087b22955e`.
2. GitHub `migration/gitlab-core-c7@c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a` = deployed CORE source.
3. GitHub `migration/gitlab-edge-011@9ad2c160f2ade19299a1d7dc2f0cfcbe6c13233c` = frozen research source.

## WHAT_TO_DO_NOW
Until a new AUD order exists: perform only a bounded READ-ONLY recheck of the three anchors and H011 provenance/readiness/safety flags; report any drift. Do not create a branch, commit, PR, deploy or D1 query merely to “continue”.

## WHAT_NOT_TO_REPEAT
- Do not redo GitHub migration/cutover.
- Do not redo SCORE-READBACK-003/004.
- Do not resume old c7 Gate B as if it were still valid.
- Do not execute SOURCE-006 just because it was once issued.
- Do not revive HYP008.
- Do not repair old GitLab CI shallow-mergebase unless a new order requires it.
- Do not merge historical PRs.
- Do not execute Issue #68.

## ACCEPTANCE / STOP CONDITIONS
READ_ONLY_RESUME_ACCEPTED only if:
- main remains the verified GitHub authority head lineage;
- H011 provenance still reports c7f7dca0... exact;
- PAPER=true, orders=false, live_capital_locked=true;
- 011 artifacts/ref remain unchanged.

STOP and return to AUD if:
- any anchor SHA differs;
- H011 no longer runs c7 or provenance is non-exact;
- safety flags regress;
- work would require deploy/write/secret access;
- work would require 011 D1 reads before 2026-09-28T00:23:35Z;
- a historical order conflicts with current remote state.

NEXT_EXACT_ACTION=Complete ORDER084 Phase 1/2 without deploy; then obey the frozen 011 time gate before any science read or runtime epoch change.
