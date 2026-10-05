# SENEX — Full Repository Audit and Cleanup — 2026-10-05

## Authority and scope

Audit baseline:

- repository: `simondalmasso/SeneX`
- audited default-branch SHA: `71dba5c5a9730ff029fe5c09551fbc28a29199d7`
- cleanup branch: `audit/full-repo-cleanup-20261004`
- H011 deployment was **not** changed by this audit
- PAPER / hard-lock / EDGE scientific authority remain unchanged

The tracked-tree census enumerated **all 211 Git-tracked files** at the audit baseline rather than sampling only application modules.

Inventory:

| Class | Count |
| --- | ---: |
| tracked files | 211 |
| UTF-8 text files | 211 |
| binary tracked files | 0 |
| total text lines | 65,092 |
| Python | 165 |
| Markdown | 26 |
| JavaScript | 3 |
| workflows YAML | 2 |
| JSON/JSONC | 4 |
| HTML/CSS | 2 |
| shell | 1 |
| dependency lock | 1 |
| byte-identical duplicate groups | 0 |
| tracked files larger than 250 KB | 0 |

One Python file carried a UTF-8 BOM; it is normalized by this cleanup and CI now rejects Python BOMs.

## Verification method

The audit combined:

1. exact `git ls-files` tracked-tree enumeration;
2. UTF-8/binary/size/hash census over every tracked file;
3. Python AST parsing and production-code pattern scans;
4. secret-pattern scanning without printing candidate values;
5. network/order/subprocess/`innerHTML` capability triage;
6. workflow and repository-surface review;
7. focused regression tests for confirmed defects;
8. SENEX Canonical CI, including the full product regression, PAPER/network safety, frontend truth, public OpenAPI, compile/static checks, Docker build, and container fail-closed smoke.

A Windows local dependency install was not treated as authority because `uvloop` is Linux-only. Canonical verification remains the Linux GitHub Actions path used by production CI.

## Confirmed defects fixed

### 1. Observability context manager could suppress real exceptions

`MetricsRegistry.time_call()` returned from inside `finally` when the metric name was unknown. In a generator context manager this swallowed an exception raised by the measured block.

TDD evidence:

- test-only head: `a301985e9963d896731df42b34dce2df431e0fa4`
- Canonical CI run: `37245854097`
- result: **1 failed, 388 passed**
- decisive failure: `DID NOT RAISE RuntimeError`

Fix: unknown metrics now skip observation without returning from `finally`, so the original exception propagates.

### 2. Binance testnet alias could bypass effective URL verification

`place_market_order()` treated the string identity `binance_testnet` as sufficient proof of testnet routing. A mutated/replaced exchange object using mainnet futures URLs could therefore pass the alias guard before reaching `create_market_order()`.

Fix:

- require the explicit `binance_testnet` identity **and**
- revalidate effective `fapiPublic` and `fapiPrivate` URLs at call time **and**
- require both URLs to contain `testnet`.

Regression tests cover both:

- alias + mainnet URLs => fail closed before ticker/order path;
- verified testnet URLs => fake testnet-only order path remains usable.

No Binance mainnet credential name is introduced and H011 still has no order capability.

### 3. Duplicate `SingleDecisionCore.record_outcome` definitions collided

`institutional_core.py` defined:

- a trade/PnL hard-learning method `record_outcome(pnl_pct, decision)`; and later
- a calibration method `record_outcome(correct)`.

Python kept only the later definition, making the hard-learning method inaccessible.

Fix:

- trade/PnL feedback is now `record_trade_outcome(pnl_pct, decision)`;
- calibration remains `record_outcome(correct)`.

Current canonical callers of `SingleDecisionCore.record_outcome()` use the one-argument calibration API, so the rename restores the dormant trade-feedback interface without breaking those callers.

### 4. Monte Carlo ruin probability polluted the Information Coefficient gauge

The research Monte Carlo endpoint wrote `ruin_probability` into `senecio_last_ic`, contradicting the metric's declared Information Coefficient semantics.

Fix:

- add `senecio_monte_carlo_ruin_probability`;
- write Monte Carlo ruin probability only to that dedicated gauge;
- keep `senecio_last_ic` reserved for IC.

### 5. Exchange connector documentation contradicted its code

The module header said it could never place an order, while the same module contains an explicit Binance testnet order path.

Fix: documentation now distinguishes public-data default behavior from the separately guarded testnet-only capability and states that mainnet order routing is forbidden.

## Repository hygiene hardened

### Secret and local-artifact hygiene

`.gitignore` now covers:

- `.env` and `.env.*`, while preserving `.env.example`;
- virtual environments;
- coverage outputs;
- mypy/ruff caches;
- build/dist/egg-info;
- macOS `.DS_Store`.

The Canonical CI secret scan already checked private-key headers, GitLab PATs, GitHub classic PATs, and OpenAI project keys. It now also checks:

- GitHub prefixed tokens `gh[pousr]_`;
- GitHub fine-grained PATs;
- NVIDIA `nvapi-` keys;
- AWS access-key IDs.

The only broader audit candidate matching a generic secret-assignment heuristic was a deliberately fake provider secret in a traceback-redaction unit test. No real credential was established.

### GitLab mirror host verification

The mirror previously generated `known_hosts` with runtime `ssh-keyscan`, trusting whichever host key was presented during that run.

Cleanup pins the published GitLab.com ED25519, RSA, and ECDSA host keys. The mirror therefore fails closed if an unexpected host identity is presented.

### Python source encoding

The BOM in `tests/test_portfolio_equity_reconcile_v2.py` is removed. Canonical CI now scans every tracked `*.py` file and rejects UTF-8 BOMs.

## Reviewed capabilities left intentionally in place

### Explicit testnet order capability

`senecio_polymarket/oracle/exchange_connector.py` contains a real **testnet** market-order method. It is not part of H011's public runtime and remains isolated behind:

- `binance_testnet` identity;
- `BINANCE_TESTNET_KEY/SECRET` only;
- `set_sandbox_mode(True)`;
- initialization-time testnet URL verification;
- call-time effective URL revalidation;
- Canonical CI static and behavioral safety checks.

This capability was hardened rather than deleted because it is a documented testnet experiment surface. It does not authorize LIVE/mainnet.

### GPTrader network transports

The GPTrader HTTP transport paths remain explicit, bounded external interfaces. The ORDER126 NVIDIA profile remains offline-only / zero-cost locked; this audit does not activate provider networking or paid calls.

### Frontend `innerHTML`

The frontend contains multiple `innerHTML` renderers. The dynamic string fields inspected in those render paths use the local HTML escaping helper. No concrete XSS defect was established, so this audit does not perform a risky wholesale DOM rewrite.

## Structural debt recorded, not mass-refactored

AST/static census of production SENEX code found:

- **374** `except Exception` handlers;
- **0** bare `except:` handlers;
- **32** broad-exception handlers whose body is only `pass`;
- **7** production Python files above 900 lines:
  - `backend/main.py` — 1,696
  - `backend/oracle_runner.py` — 1,031
  - `backend/supabase_client.py` — 1,464
  - `oracle/exchange_connector.py` — 2,597
  - `oracle/institutional_core.py` — 1,636
  - `oracle/oracle_lab.py` — 1,094
  - `oracle/predict_only.py` — 977

Those are maintainability risks, but bulk exception narrowing or file splitting in the same safety cleanup would create a much larger behavioral delta than the evidence supports. They are therefore documented debt, not silently rewritten.

## Historical and research artifacts retained

- The old H011 SHA inside the dated audit is explicitly historical/superseded context, not stale current-state authority.
- `research/edge/order099/results/2026-10-04/RUN_MANIFEST.json` is retained as research evidence, not treated as a generated cache.
- Archived user material is preserved as historical source material rather than rewritten for style.

Scientific authority remains:

```text
INCREMENTAL_EDGE_NOT_DEMONSTRATED
EDGE=UNPROVEN
```

PAPER PnL remains operational simulation evidence only.

## Branch/reference hygiene

The repository currently has **141 branches including `main`**. Examples include historical migration, audit, feature, docs, temporary rebase, PAPER and ops refs.

A focused review of `tmp/*`, recent docs, ORDER126, BINANCE_SIM and deploy branches shows many are Git-diverged from current `main` because their work was squash-merged/rebased and they still preserve unique commit objects.

For that reason this audit does **not** auto-delete branches based only on naming or tip ancestry. Branch pruning should be a separate ref-governance operation with an explicit preservation policy; deleting these refs during code cleanup would discard useful provenance and would also propagate through the GitHub-to-GitLab mirror.

## Acceptance gate

This cleanup is acceptable only if its exact final PR head passes the complete SENEX Canonical CI:

- exact checkout + canonical lineage;
- locked dependency install;
- focused authority/readiness tests;
- full product regression;
- PAPER/network safety;
- frontend truth tests;
- runtime safety boundary and GET-only public OpenAPI;
- credential scan;
- compile + BOM + diff static checks;
- canonical Docker build as non-root;
- container fail-closed smoke.

No deploy is implied by merging this audit. H011 deployment requires a separate explicit operational gate and post-deploy acceptance.
