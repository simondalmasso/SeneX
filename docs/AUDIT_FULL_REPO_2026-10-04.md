# SENEX — Full tracked-repository audit and cleanup — 2026-10-04

> Local date: 2026-10-04 (America/Argentina/Cordoba). CI timestamps cross into 2026-10-05 UTC.
>
> This audit inventoried and scanned **100% of Git-tracked files** at base commit
> `71dba5c5a9730ff029fe5c09551fbc28a29199d7`. That is not a mathematical proof
> that no undiscovered defect exists. Runtime/live evidence remains a separate authority.

## Scope and method

A clean disposable checkout of canonical `main` was verified at
`71dba5c5a9730ff029fe5c09551fbc28a29199d7` before the cleanup branch was created.

Tracked-file census:

```text
tracked files: 211
text files: 211
binary files: 0
total text lines: 65,092
Python files: 165
Markdown files: 26
JavaScript files: 3
YAML workflow files: 2
HTML files: 1
CSS files: 1
shell files: 1
files >250 KB: 0
byte-identical duplicate groups: 0
```

The audit covered:

- every path returned by `git ls-files`;
- Python AST/syntax parsing and compile behavior;
- TODO/FIXME/HACK markers;
- credential/secret heuristics;
- order/wallet/withdraw/network/subprocess/eval/exec/shell patterns;
- public/runtime mutation boundaries;
- frontend `innerHTML` sinks;
- dependency lock and CI workflow structure;
- duplicate method definitions exposed by focused inspection;
- documentation/runtime truth boundaries;
- Canonical CI regression, PAPER/network safety, frontend truth, OpenAPI,
  compile/static checks, Docker build and fail-closed smoke.

## Inventory findings

The initial static pass produced 3 TODO/FIXME/HACK markers. Two are inside preserved
historical user-material archive text. The one active code TODO was the Monte Carlo
ruin probability being written into `senecio_last_ic`; that defect is fixed by this
cleanup.

One generic secret heuristic matched a deliberately fake provider-secret value in
`tests/test_order092_external_client.py`. No real credential was identified.

One old H011 SHA appears in the dated audit history. It is explicitly historical and
superseded in that document, so it is preserved rather than rewritten.

The broad pattern pass found 55 potentially sensitive occurrences:

```text
create/place-order patterns: 15
withdraw patterns: 8
subprocess calls/patterns: 13
urllib request patterns: 2
innerHTML sinks: 14
shell=True text/pattern: 1
eval pattern: 1
exec pattern: 1
```

Manual triage found that almost all are tests, static safety guards, documentation,
or bounded read/network infrastructure. The material order-capable path is the
explicit Binance testnet experiment in `oracle/exchange_connector.py`; it is not
mounted into H011's public runtime and is strengthened by this cleanup.

All 14 frontend `innerHTML` sites were manually reviewed. Dynamic string fields are
escaped with `esc()` or flow through `sourceRow()`, which escapes name/detail/claim
fields; remaining interpolation is static or numeric formatting. No confirmed
frontend injection path was found in this set.

## Confirmed defects fixed

### 1. Observability context manager suppressed exceptions

`MetricsRegistry.time_call()` returned from its `finally` block when the metric name
was unregistered. In Python, that return can suppress an exception raised inside the
measured block.

TDD evidence:

- PR #135, run `37245854097`, exact head `a301985...`;
- full regression: **1 failed, 388 passed**;
- decisive failure: expected `RuntimeError("boom")`, but it was suppressed.

Fix: never return from the `finally`; skip metric observation when the metric is
unknown while allowing body exceptions to propagate.

### 2. Binance testnet alias could bypass effective URL validation

`place_market_order()` treated the string alias `binance_testnet` as sufficient
testnet evidence. A replaced/mutated exchange object under that alias could therefore
reach ticker/order code while its effective futures URLs pointed at mainnet.

Red evidence:

- run `37245944476`;
- test reached `fetch_ticker()` before the expected safety abort.

Fix: order execution now requires both the explicit `binance_testnet` alias and
effective `fapiPublic` + `fapiPrivate` URLs containing `testnet` at call time.
A positive test also preserves the verified-testnet path.

### 3. Trade-feedback method was overwritten by calibration feedback

`SingleDecisionCore` defined `record_outcome` twice. The later
`record_outcome(correct: bool)` replaced the earlier trade-feedback implementation
at class creation time.

Red evidence:

- run `37245944476`;
- `record_trade_outcome` was absent.

Fix: the trade/PnL path is explicitly named `record_trade_outcome(pnl_pct, decision)`;
`record_outcome(correct: bool)` remains the calibration API used by existing runtime
call sites.

### 4. Monte Carlo ruin probability corrupted the IC metric

The Monte Carlo endpoint wrote ruin probability into `senecio_last_ic`, whose
documented meaning is Information Coefficient.

Red evidence:

- run `37245944476`;
- dedicated gauge test failed because no ruin-probability metric existed.

Fix: add and use `senecio_monte_carlo_ruin_probability`.

### 5. CI static safety check encoded an obsolete implementation string

After strengthening the testnet guard, Canonical CI still required the old literal
`"testnet" not in fapi_private`.

Intermediate evidence:

- run `37246044794`;
- full regression: **392 passed**;
- PAPER/network safety: **26 passed**;
- frontend truth: **5 passed**;
- only the runtime/static boundary step failed on that obsolete string assertion.

Fix: CI now checks the stronger effective-routing contract:
`effective_testnet`, both futures URL keys, and per-URL `"testnet" in url.lower()`.

## Repository hygiene changes

- removed the UTF-8 BOM from `tests/test_portfolio_equity_reconcile_v2.py`;
- expanded `.gitignore` for local `.env*`, venvs, coverage, caches and build artifacts;
- expanded tracked-secret scanning for GitHub prefixed/fine-grained tokens,
  NVIDIA `nvapi-` keys and AWS access keys;
- corrected `exchange_connector.py` documentation so it no longer claims the whole
  module is incapable of orders while an explicitly guarded testnet-only path exists.

## Dependencies and build chain

`requirements.lock` contains 60 fully pinned packages with hashes and declares its
pip-compile provenance under Python 3.11.

A Windows-only local install attempt failed on `uvloop`, which does not support
Windows. This is not treated as a production defect because canonical CI/runtime use
Linux and Canonical CI installs the same hashed lock successfully.

Canonical workflow positives:

- workflow permissions are `contents: read`;
- checkout is pinned by commit and uses `persist-credentials: false`;
- exact candidate SHA and canonical lineage are verified;
- tracked-file credential patterns are scanned;
- public `main_real` OpenAPI is required to contain zero mutation routes;
- runtime private-order symbols are rejected from `oracle_runner.py`;
- Docker is built at exact SHA and required to run non-root;
- container smoke is network-isolated and verifies PAPER hard lock and fail-closed readiness.

GitHub currently warns that pinned actions targeting Node.js 20 are being forced onto
Node.js 24. That is maintenance debt, not a SENEX runtime failure; action-pin upgrades
should be reviewed separately rather than silently floated during this code cleanup.

## Deliberately not mass-refactored

The audit counted substantial historical/architectural debt:

```text
production broad except Exception handlers: 374
production broad handlers with pass-only body: 32
production files >900 lines: 7
detected explicit network-call sites: 18
environment reads: 45
```

Largest files include `oracle/exchange_connector.py`, `backend/main.py`,
`backend/supabase_client.py`, `oracle/institutional_core.py`,
`oracle/oracle_lab.py`, `backend/oracle_runner.py`, and `oracle/predict_only.py`.

These are not mechanically rewritten in this PR. A mass exception/refactor pass would
change behavior across legacy, research, testnet and runtime surfaces and would be
higher risk than the defects it attempts to clean. They remain explicit technical debt
for bounded subsystem-specific follow-ups.

Historical archive material is also preserved as evidence, including wording/encoding
that may look untidy. Archive fidelity outranks cosmetic normalization.

## Runtime boundary

This cleanup does **not** deploy H011, enable LIVE, connect real Binance credentials,
or change the scientific edge verdict.

At the last direct H011 verification before this audit:

```text
deployed source_commit=96c41f123175f597788911ab7f52a18ed77c7cdb
trade_mode=PAPER
orders_enabled=false
live_capital_locked=true
hard_paper_lock=true
BINANCE_SIM simulation_only=true
BINANCE_SIM live_orders_possible=false
EDGE=UNPROVEN
research_verdict=INCREMENTAL_EDGE_NOT_DEMONSTRATED
```

Repository cleanup and production deployment remain separate gates.

## Final acceptance requirement

This audit is complete only when the final exact PR head passes all Canonical CI stages:

1. exact checkout + lineage;
2. locked dependencies;
3. focused authority/readiness;
4. full regression;
5. PAPER/network safety;
6. frontend truth;
7. runtime safety + public OpenAPI + secret scan;
8. compile + diff check;
9. exact Docker build/non-root;
10. fail-closed container smoke.

The final run/commit is recorded in the PR conversation after completion.
