# ORDER088 — External Reference Audit: CABBAGE

STATUS=RESEARCH_COMPLETE
SOURCE=https://github.com/sopersone/cabbage-trading-machine
SOURCE_PIN=530c2c68278addfe33e2c4b817eda5d72b6a661d
UPSTREAM=https://github.com/coding-kitties/investing-algorithm-framework
UPSTREAM_PIN=fff7436f12ea95a2e5f794ce6800663fc21ef8ec
SOURCE_LICENSE=Apache-2.0
USE=REFERENCE_AND_TEST_ORACLE_ONLY
IMPORT_FRAMEWORK=NO
VENDOR_FRAMEWORK=NO
IMPORT_STRATEGY=NO
LIVE_PATH=NO
BROKER_PATH=NO
D1=NO

## Executive disposition

CABBAGE is useful to SENEX GPTrader as a source of engineering patterns and adversarial/integration test ideas, especially for P2 PAPER lifecycle and P6 execution-assumption validation.

It is NOT useful as:
- a replacement execution engine;
- a runtime dependency;
- a GPTrader strategy;
- a source of profitability evidence;
- a reason to add CCXT/live exchange credentials;
- a reason to change SENEX prediction logic.

Binding principle:

BORROW_TESTS_AND_METHODS > IMPORT_FRAMEWORKS

## Fresh source facts

At review time:
- CABBAGE main = 530c2c68278addfe33e2c4b817eda5d72b6a661d.
- Repository created 2026-09-24; only two visible commits at the pinned review point.
- Repository metadata: Python, Apache-2.0, 245 stars, 59 forks.
- README states it embeds/uses Investing Algorithm Framework 9.0.0a18 as the actual runtime.
- CABBAGE validation pins upstream commit fff7436f12ea95a2e5f794ce6800663fc21ef8ec.
- Upstream Investing Algorithm Framework is a materially older/larger project and remained active at review time.
- ToolCheck by M8ven: Caution, trust_score=60/100; verdict_reason: "Trust score 60/100 — some concerns to review". No specific top findings were returned.
- The CABBAGE repository is too new for stars/forks to count as maturity evidence.

## What CABBAGE actually demonstrates

CABBAGE wraps the upstream framework with:
- explicit commands: doctor, backtest, paper, live;
- explicit paper/live construction;
- a guard that prevents an upstream environment override from silently converting an explicitly selected paper mode into live mode;
- state directory separation by mode/market/pair;
- RSI/EMA long-only strategy logic using closed candles;
- fixed ticket, fixed stop and configured fees;
- event-driven backtest;
- paper integration through the original framework;
- atomic latest-run report replacement;
- live wiring to the upstream CCXT executor when credentials exist.

Its validation document says:
- 12 selected upstream paper-trading tests passed;
- 6 CABBAGE integration tests passed;
- full PAPER BUY -> fill -> SELL -> fill accounting path was exercised with synthetic market quotes but real framework services/SQLite accounting;
- historical event-driven backtest on 2024-06-10..2024-06-20 produced zero trades;
- no profitability claim follows from that backtest;
- public BITVAVO access failed in the validation environment;
- no real live order was executed;
- full upstream test suite was not run.

## KEEP — high-value patterns for SENEX GPTrader

### K1 — Hard PAPER mode selection cannot be silently overridden

CABBAGE explicitly rejects upstream environment overrides that could change paper behavior.

Borrow:
- GPTrader PAPER mode must be construction-time explicit.
- No environment variable or inherited runtime flag may silently route GPTrader to another execution mode.
- Add a regression test that injects hostile/contradictory env flags and proves GPTrader remains PAPER-only or fails closed.

Do NOT borrow:
- a configurable live mode.

### K2 — Namespaced state by mode/context

CABBAGE uses separate runtime directories by mode, market and pair.

Borrow for ORDER086 P2:
- keep GPTrader treatment state/journal physically namespaced from SENEX control;
- include policy/run namespace where needed;
- restart tests must prove the control journal remains byte-unchanged.

This reinforces the existing ORDER085 design rather than changing architecture.

### K3 — Full lifecycle PAPER integration fixture

The strongest CABBAGE contribution is its integration test:
- signal creates BUY;
- PAPER order remains OPEN;
- later bar fills BUY;
- close signal creates SELL;
- later bar fills SELL;
- orders end CLOSED;
- trade/accounting/report persist.

Borrow for GPTrader P2:
- create a deterministic synthetic-quote lifecycle fixture using SENEX existing RiskKernel / ExecutionEngine / TradeJournal classes;
- assert TAKE -> accepted/rejected -> fill/close lifecycle;
- assert ABSTAIN creates no execution record;
- assert journal/state survive restart;
- assert SENEX native PAPER control files are untouched.

Do not copy CABBAGE code; reproduce the test concept using SENEX classes.

### K4 — Separate integration correctness from profitability evidence

CABBAGE correctly labels its zero-trade historical backtest as an integration check, not an edge/profit claim.

Borrow:
- GPTrader path tests prove wiring/accounting only.
- No backtest PASS can emit EDGE.
- Zero trades is valid path evidence but contains no trade-outcome evidence.
- Keep execution correctness and scientific verdict layers separate.

### K5 — Event-driven validation after faster/simple screening

Upstream documentation separates vector screening from event-driven final validation and explicitly notes that vector execution can hide realistic order-routing, partial-fill and capital constraints.

Borrow methodologically:
- deterministic/scientific filtering may remain simple;
- final PAPER execution claims should be checked through the actual SENEX event/execution path;
- execution-sensitive conclusions must include fill/cost assumptions.

Do NOT add a second vector engine to SENEX solely because upstream has one.

### K6 — Partial-fill / order-lifecycle adversarial cases

Upstream has explicit models/tests for:
- partial fills;
- volume-limited fills;
- cancellation;
- fill-based accounting;
- fee attribution;
- stop/take-profit fill costs;
- order persistence and restart.

Borrow as TEST IDEAS:
- partial TAKE fill;
- partially closed position;
- duplicate fill prevention after restart;
- cancellation and late fill;
- fee/slippage attribution;
- open order surviving restart;
- deterministic replay under the same fill assumptions.

Adopt only cases relevant to SENEX existing execution primitives.

### K7 — Atomic report publication

CABBAGE writes latest-run.json through a temporary file followed by replace.

Borrow where a mutable snapshot file is needed:
- write temp;
- fsync/close as appropriate;
- atomic replace;
- append-only authoritative journals remain separate.

This is compatible with GPTrader verdict/state snapshot publication.

### K8 — Closed-candle guard as a no-lookahead precedent

CABBAGE removes incomplete candles before signal generation.

For SENEX this is not new logic, but the test principle is useful:
- every decision input must have explicit observation-time semantics;
- incomplete/post-T0 evidence must be excluded;
- write a fixture proving a later/incomplete observation cannot change an already sealed decision packet.

## DROP / DO NOT IMPORT

### D1 — RSI/EMA strategy logic

DROP.

Reason:
- GPTrader is a selection/evaluation treatment over sealed SENEX predictions, not a new signal generator.
- Importing RSI/EMA changes the scientific question and becomes unapproved model/strategy tuning.
- CABBAGE's own supplied historical validation produced zero trades over the tested interval, which is neither positive nor negative predictive evidence.

### D2 — Full Investing Algorithm Framework runtime

DROP AS RUNTIME DEPENDENCY.

Reason:
- SENEX already owns RiskKernel, ExecutionEngine, TradeJournal and PAPER control.
- Importing the framework creates a second execution authority and duplicates persistence/risk/order semantics.
- Large dependency and operational surface is unjustified for ORDER086.

Use upstream only as reference/test oracle.

### D3 — CCXT live executor / credentials

DROP.

CABBAGE intentionally exposes a real live command and CCXT executor.

GPTrader requirements are the opposite:
- no broker;
- no wallet/signer;
- no exchange account credentials;
- no real-order path;
- not even a disabled-by-config live execution surface.

Do not copy or expose these modules through GPTrader.

### D4 — Broker-native sandbox as GPTrader architecture

DROP FOR V1.

It still introduces broker credentials, venue-specific behavior and a technical bridge toward live execution.

SENEX namespaced PAPER simulation is the required authority for ORDER086.

### D5 — CABBAGE default parameters as research priors

DROP:
- BTC/EUR;
- 2h;
- 50 EUR ticket;
- 5% stop;
- 0.25% fee;
- RSI/EMA thresholds.

These are software defaults in another strategy and are not evidence for SENEX/GPTrader parameters.

### D6 — Repository popularity as validation

DROP.

The CABBAGE repository was only days old at review time. Stars/forks do not establish scientific validity, safety, maturity or profitable execution.

### D7 — Promotional or upstream profitability narratives

DROP.

Only independently reproduced execution/scientific evidence belongs in SENEX verdicts.

## Concrete ORDER086 application

### P2 — namespaced PAPER book

Add/retain tests inspired by CABBAGE:
1. GPTrader TAKE creates only GPTrader namespaced execution state.
2. ABSTAIN creates zero fills.
3. accepted TAKE -> open -> fill -> close -> closed lifecycle with deterministic synthetic market evidence.
4. kernel rejection remains distinct from ABSTAIN.
5. restart preserves open/closed treatment state without double fill.
6. SENEX native PAPER control journal/hash unchanged.
7. hostile env/live-style flags cannot alter PAPER-only behavior.
8. snapshot/report write is atomic if a mutable state snapshot is used.

### P4 — Decision MCP

Do NOT expose any upstream/CABBAGE live executor concept.
No CCXT account calls.
No state payload that reveals recent outcomes.

### P6 — execution assumptions

Borrow only the methodology:
- explicit fees/slippage/fill assumptions;
- stress execution assumptions;
- partial-fill/cancellation fixtures when SENEX execution supports them;
- separate execution-model sensitivity from signal usefulness.

Do not import upstream Monte Carlo/vector infrastructure into ORDER086.

## Security / trust disposition

ToolCheck result:
- verdict_label=Caution
- trust_score=60/100
- verdict_reason="Trust score 60/100 — some concerns to review"
- no specific top findings returned.

Interpretation:
- this does not prove maliciousness;
- it is an additional reason not to connect/import the repo as an execution dependency;
- source pinning + selective manual reference is appropriate.

## Deep-search cross-check

Independent search connectors found the upstream framework documentation and repository describing:
- same strategy class across backtest/paper/live;
- event-driven execution;
- partial fills;
- pluggable slippage/commission;
- persistence/state recovery;
- walk-forward/OOS/Monte Carlo tooling.

These strengthen the conclusion that the upstream is rich as a REFERENCE, but also strengthen the reason not to import it into SENEX: it overlaps heavily with SENEX's existing execution/research responsibilities.

Search limitations during this audit:
- Tavily request hit plan usage limit.
- Liner deep research had no credits.
- Exa + Parallel Search + GitHub primary evidence were sufficient for the disposition.

## Final disposition

```text
SOURCE=cabbage-trading-machine@530c2c68278addfe33e2c4b817eda5d72b6a661d
UPSTREAM=investing-algorithm-framework@fff7436f12ea95a2e5f794ce6800663fc21ef8ec

USE=REFERENCE_AND_TEST_ORACLE_ONLY
BORROW=PAPER_MODE_GUARD,STATE_NAMESPACING,FULL_LIFECYCLE_TESTS,PARTIAL_FILL_TEST_IDEAS,ATOMIC_REPORT,EVENT_DRIVEN_VALIDATION_METHOD
DROP=RSI_EMA_SIGNAL,CCXT_LIVE,BROKER_SANDBOX,FULL_FRAMEWORK_IMPORT,DEFAULT_PARAMETERS,PROMOTIONAL_CLAIMS

ORDER086_PRODUCT_CODE_CHANGED=NO
MAIN_CHANGED=NO
DEPLOYMENTS=0
D1_READS=0
D1_WRITES=0
LIVE=NO
REAL_ORDERS=0
CAPITAL=0
```
