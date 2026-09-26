# AUD_CANON.md

PROJECT=SENEX  
PURPOSE=Sistema de investigación/observabilidad de mercado y ejecución PAPER-only; EDGE sigue no probado.  
REPO=https://github.com/simondalmasso/SeneX  
LIVE=https://h011-web--senecio-h011--wbjggn89fnf8.code.run/

## LAST_VERIFIED / BRANCH / HEAD
LAST_VERIFIED=ORDER084 canonical convergence phase
BRANCH=main  
ORDER084_START_MAIN=99ccac05a552d86d129a2675b6ab1c2604a2770f
GITLAB_MAIN=cc7c9d2ebe933f3696723f2512d508232fbc2895  
DEPLOYED_CORE=c7f7dca0e7b9f9bc3a4bf8371ae17b4eb2a65e8a  
EDGE_011=9ad2c160f2ade19299a1d7dc2f0cfcbe6c13233c

## CANONICAL LINKS
- GitHub code/CI: https://github.com/simondalmasso/SeneX
- GitLab historical/control evidence: https://gitlab.com/simondalmasso/SENEX
- GitLab Control Plane #7: https://gitlab.com/simondalmasso/SENEX/-/work_items/7
- Drive archive/canon: https://drive.google.com/drive/folders/1yYryhMTzJhDxtxQyGMWasQLfY1P1awqg
- H011: https://h011-web--senecio-h011--wbjggn89fnf8.code.run/
- P01: https://p01--seneciobot--wbjggn89fnf8.code.run/

## CURRENT STATE
- GitHub migration/cutover is closed; GitHub is declared CODE/CI authority.
- GitHub main contains the deployed CORE c7 lineage through ORDER084 history-preserving convergence plus canonical GitHub CI/canon docs.
- H011 current evidence reports source_commit=c7f7dca0..., provenance exact=true, READY, PAPER, orders disabled, live capital locked.
- H011 still runs exact c7f7dca0... until ORDER084 later authorizes a deliberate final exact-head deploy.
- GitHub main branch protection is currently off.
- GitHub Issue #68 is VOID/closed; ORDER084 Issue #69 is the current master order.
- Historical Drive/GitLab evidence remains preserved; implementation authority is ORDER084 Issue #69 on GitHub.
- SCORE-READBACK-004 proved existing persisted audit is sufficient for score/outcome discrimination; no new score ledger is justified.
- TOP2 retrospective 010 was INCONCLUSIVE.
- TOP2-PROSPECTIVE-011 is COLLECTING/FROZEN with PROGRESS_N=0 and NO_EARLY_LOOK.
- HYP008=PAUSE; READY_FOR_REARM=false.
- SOURCE-006 remains unresolved and must be freshly reinvestigated after frozen 011 terminalization under ORDER084.
- Gate B on c7 was superseded/not accepted; Gate C remains pending.
- EDGE=UNPROVEN; LIVE=NO; REAL_ORDERS=0; CAPITAL=0.

## DONE
- Exact GitLab objects mirrored to GitHub.
- Canonical GitHub CI created and post-cutover CI passed.
- Post-cutover science parity PASS and safety parity PASS.
- c7 D1 cold-hydration/readiness repair deployed and observed READY before later holds.
- Existing audit persistence validated as sufficient by bounded SCORE-READBACK-004.
- PAPER safety locks remain the governing operating mode.

## ACTIVE WORK
- ORDER084 is actively authorized; ARQ1 is the sole writer/implementer/integrator/deployer.
- Phase 1/2 convergence is non-deploying; H011 remains exact c7 and 011 remains frozen.
- 011 remains frozen until its preregistered read gate.

## PENDING
1. Reconcile GitHub main vs deployed CORE c7 without deploying or rewriting history.
2. Decide the minimal canonical convergence path and then issue the next correctly numbered order (expected next free historical number: ORDER084, re-verify before issuance).
3. Protect GitHub main after authority reconciliation.
4. At/after 2026-09-28T00:23:35Z, execute 011 only under its frozen contract.
5. Reassess SOURCE-006 and Gate B/C only after the authority/runtime reconciliation.

## BLOCKERS / RISKS
- CANON_DIVERGENCE: GitHub main != deployed H011 CORE.
- main protection disabled.
- SOURCE-006/Gate-B history contains superseded decisions; do not revive stale orders blindly.
- `up_prob` remains an unvalidated score, not a calibrated probability.
- 011 early-look contamination would invalidate the prospective test.
- Legacy PRs #24/#25/#36 are historical draft lanes, not current implementation authority.

## DO_NOT_TOUCH
- Do not deploy GitHub main over H011 merely to make SHAs match.
- Do not mutate/delete `migration/gitlab-core-c7`, `migration/gitlab-edge-011`, or archive refs.
- Do not run normal 011 D1 reads before 2026-09-28T00:23:35Z.
- Do not inspect interim 011 outcomes/AUC/delta.
- Do not activate HYP008, TOP1/TOP3, Agentics, LIVE, wallets, orders or capital.
- Do not merge legacy PRs as a shortcut.
- Do not execute VOID Issue #68.

## AUTHORITIES / GATES
CODE_CI_AUTHORITY=GitHub
DEPLOYED_RUNTIME_AUTHORITY=H011@c7f7dca0... until deliberately reconciled  
HISTORICAL_CONTROL_EVIDENCE=GitLab Work Item #7 + Drive canon  
PAPER_ONLY=true  
ORDERS_ENABLED=false  
LIVE_CAPITAL_LOCKED=true  
EDGE=UNPROVEN  
READY_FOR_REARM=false  
011_GATE_NOT_BEFORE=2026-09-28T00:23:35Z

## NEXT EXACT ACTION
NEXT_EXACT_ACTION=ARQ1: complete ORDER084 Phase 1/2 PR, current-head CI and main protection without deploy; then obey the frozen 011 time gate.
