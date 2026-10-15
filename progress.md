# REA Engine Lab — Progress / Evidence Ledger

- **Status:** Implementation and verification complete in an isolated feature branch; merge and deployment remain pending review.
- **Branch:** `feat/rea-engine-lab-20261014`
- **Base commit:** `583e11588cea8594db8625a01dc0305ec826f008`
- **Plan:** `docs/specs/2026-10-14-rea-engine-lab-implementation-plan.md`
- **Design:** `docs/specs/2026-10-14-rea-engine-lab-design.md`

## Completed tasks

- Task 1 — contracts and Decimal ledger: complete.
- Task 2 — deterministic replay, pending orders, expiry and cancellation: complete.
- Task 3 — Grid, DCA and Combo clean-room strategies: complete.
- Task 4 — local CSV evaluation, separate benchmarks, cost stress, hashes, CLI and README: complete.

## Verification

- Focused tests: `python -m unittest discover -s tests -p "test_rea_engine_*.py" -v` — **28 tests passed**, exit code 0.
- Compilation: `python -m compileall -q research/rea_engines tests/test_rea_engine_accounting.py tests/test_rea_engine_replay.py tests/test_rea_engine_strategies.py tests/test_rea_engine_evaluation.py` — exit code 0.
- Static import scan: no network/exchange SDK/runtime imports in `research/rea_engines/*.py`.
- Python whitespace scan: no trailing whitespace in production Python sources.
- Placeholder scan: no TODO/TBD/FIXME/NotImplementedError/PLACEHOLDER in production Python sources.
- Canonical dataset hash verified against exact local bytes; no network fetch or substitution.
- Full existing SENEX pytest suite: **not run** because the SentinelX Python environment has no `pytest`, no alternate local pytest interpreter was found in the bounded search, and the local pip wheel cache is empty. No dependency was installed from the network.

## Canonical dataset and report

- Input: local REA file `INDEPENDENT-PAPER-BOT/data/binance/BTCUSDT-4h-2020-01_to_2026-09.csv`.
- Source SHA-256: `7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146` (verified).
- Full rows: 14,789. Primary OOS window: 2022-01-01T00:00:00Z to 2026-09-30T20:00:00Z, 10,404 rows.
- Gaps: one 8-hour gap in the full source, outside the OOS interval; zero gaps inside OOS.
- Report path: `research/rea_engines/runs/strategy-comparison-20261014.json`.
- Report size: 11,296,010 bytes.
- Raw report file SHA-256: `08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d`.
- Canonical report payload SHA-256: `465e48502d88c398ec9f099179d291af240ad26cc14c78ef9a3c37750530bc7b` (self-check passed; rerun produced the same digest).
- Config SHA-256: `4ca902bffe31ff4f7924ab238c2c2fdd9a1334570a6b0b9d325e803802eae5da`.
- Cost profile/stress SHA-256: `dd82a30d6f68d170e6b0cc77e6ec23492446525321bc8d1e03208208f8d69c55`.
- Fill policy: `ohlcv-next-open-resting-limit-v1`.
- Fill-policy SHA-256: `62e3f83898a736bf28d358fdefb722e8aecd6344e30a944123c78462f9149b0a`.

## Historical results — net of modeled costs

All strategy returns are percentages of 10,000 USDT starting equity over the same 10,404-candle OOS interval. Exposures differ; these are not risk-normalized comparisons.

| Candidate | Net return | Max drawdown | Fills | Time in market |
|---|---:|---:|---:|---:|
| Grid | +0.1625% | 0.1596% | 26 | 2.15% |
| DCA | +1.8645% | 1.5979% | 179 | 99.36% |
| Combo | +0.3205% | 0.2399% | 22 | 1.13% |
| Buy-and-hold, 100 USDT quote allocation | +0.7846% | 1.4082% | 1 | 99.99% |
| Buy-and-hold, fixed 0.02 BTC | +7.3457% | 11.5863% | 1 | 99.99% |

DCA cost stress: +1.8645% at baseline, +1.6870% at 2x costs, +1.4193% at 3x costs. Its annual reset subperiod returns were 2022 −1.1882%, 2023 +1.3161%, 2024 +1.4568%, 2025 +0.0117%, and 2026 YTD −0.0394%. The inconsistent annual results, exposure differences, and OHLCV fill assumptions prevent a claim of robust edge.

## Safety state

`mode=historical-paper`, `network_calls=0`, `live_orders_submitted=0`, `zero_spend=true`, `EDGE=UNPROVEN`. No runtime/BINANCE_SIM integration, live/testnet orders, API credentials, network calls, merge, push, PR, deployment, or CI trigger was performed.

## Resume audit 2026-10-14

- PowerShell re-verified the implementation plan at 21 checked tasks and 0 unchecked tasks, `git diff --check` exit 0, report status `completed`, report raw SHA-256 `08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d`, source-data SHA-256 `7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146`, and the report safety fields (`EDGE=UNPROVEN`, `live_orders_submitted=0`, `network_calls=0`, `zero_spend=true`).
- The current SentinelX script execution context cannot resolve `python`, `python3`, or `py`; no `python.exe` was found inside this workspace. Therefore this audit could not re-run the focused unittest suite or compileall. The previously recorded 28 passing focused tests remain historical evidence, not a fresh test run in this audit.
- No interpreter/dependency installation, network access, remote publication, or production action was attempted. If an approved local Python runtime becomes available, repeat focused tests and static verification only.

## Fresh verification run 2026-10-14

- Focused REA suite rerun under the SentinelX-managed Python 3.14.2 interpreter: `python -m unittest discover -s tests -p "test_rea_engine_*.py" -v` — **28 tests passed**, exit 0.
- Focused REA `compileall`: exit 0. Repository-wide syntax compilation of `senecio_polymarket`, `tests`, and `research`: exit 0.
- REA CLI `--help` and all six REA module imports: passed. Static AST scan of `research/rea_engines/*.py`: no forbidden network/exchange/runtime imports, trailing whitespace, or placeholder markers.
- Offline reproducibility rerun using the exact local CSV and expected SHA-256 completed with exit 0. Regenerated JSON matched the committed-to-workspace report byte-for-byte: raw SHA-256 `08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d`; canonical payload SHA-256 `465e48502d88c398ec9f099179d291af240ad26cc14c78ef9a3c37750530bc7b`. Source SHA-256 remained `7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146`.
- Report safety verified: `EDGE=UNPROVEN`, `mode=historical-paper`, `live_orders_submitted=0`, `network_calls=0`, `zero_spend=true`.
- Full SENEX pytest suite remains **not run**: pytest is absent from the SentinelX-managed Python 3.14.2 environment; 32 of 65 test files directly reference pytest, and a bounded local workspace scan found no alternate `python.exe`, `pytest.exe`, or installed pytest package. No package install or network access was attempted.

## Final repository continuation — 14 October 2026

- Repository: simondalmasso/SeneX; feature branch: feat/rea-engine-lab-20261014; base HEAD at implementation start matched main (583e11588cea8594db8625a01dc0305ec826f008).
- The latest full-suite run used a temporary external import shim for the workstation's missing sse_starlette module. python -m pytest -q --disable-warnings: **697 passed, 93 warnings, exit 0 in 57.33 seconds**. The shim was removed; it does not verify real SSE transport.
- Additional focused evidence already recorded: dashboard snapshot 9/9; public Binance/data/router plus paper execution contract bundle 51/51; independent Node paper bot 32/32; clean-room engine families 31/31; regime classifier 4/4.
- git diff --check passed before publication preparation. The source package remains paper-only and public-data-read-only; EDGE=UNPROVEN; production order integration is not approved.
- Raw ETH candle data/source archives, large generated JSON/CSV run outputs, bytecode and backup files are not part of the public source change. No deployment, account credential use, testnet/live orders or runtime order wiring was performed.
### Clean Git worktree dashboard check

A clean detached Git worktree without the excluded local reports/data ran tests/test_rea_dashboard_snapshot.py: 2 passed, 8 skipped, 1 warning. The two always-on tests validated frontend safe rendering and the embedded static snapshot contract; the skipped cases are tied to workstation-only report/data/inventory inputs. The worktree was removed after verification.