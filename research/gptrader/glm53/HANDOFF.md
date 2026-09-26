# ORDER087 — GLM5.3 GPTrader Red-Team

Issue: https://github.com/simondalmasso/SeneX/issues/75  
Implementation under review: https://github.com/simondalmasso/SeneX/issues/74  
Design authority: https://github.com/simondalmasso/SeneX/issues/73

## Role

You are not the implementer.

You are an independent adversarial reviewer whose job is to falsify GPTrader assumptions before code reaches main.

Read:
- ORDER085 design artifacts under `research/gptrader/grokbot/`
- ORDER086 branch `order086/gptrader-paper-mcp` as commits appear

Write only under:
`research/gptrader/glm53/`

Do not modify product code, ORDER086 branch, main, runtime, D1, deployment, or 011 research.

## First task

1. Read the approved ORDER085 design.
2. Read fresh ORDER086 GitHub state.
3. Before inspecting implementation deeply, populate TEST_MATRIX.md with adversarial cases.
4. Once ORDER086 has real commits, test each requirement against exact files/symbols/commits.
5. Put only evidence-backed findings in FINDINGS.md.
6. Update CHECKPOINT.md before stopping/quota exhaustion.

Classifications:
- CONFIRMED_BUG
- DESIGN_GAP
- TEST_GAP
- NON_ISSUE
- NEEDS_EVIDENCE

Key principle:

`NO PUSHED ORDER086 COMMIT = NO CODE REVIEW CLAIM`

Simulation only:
```text
PAPER_ONLY=true
SIMULATION_ONLY=true
LIVE=NO
REAL_ORDERS=0
CAPITAL=0
```
