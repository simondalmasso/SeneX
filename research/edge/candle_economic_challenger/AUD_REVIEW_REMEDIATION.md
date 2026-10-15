# ORDER197 / ARQ — AUD P1 remediation receipt

Source review: https://github.com/simondalmasso/SeneX/issues/23#issuecomment-6050048732

**Review disposition:** implemented as research-code changes; still **CHANGES_REQUESTED pending independent AUD re-review**. No authentic original T0 bytes or original 1h label custody admitted; `BLOCKED_ARTIFACT_BYTES`, `EDGE=UNPROVEN`.

| Finding | RED | GREEN |
| --- | --- | --- |
| P1-F01 fixed cost policy | `KeyError: oos_actions` | Train A/E/B/C/D only at 15bps; frozen per-T0 action vectors replayed identically at 10/15/20/30bps, including non-FLAT fixture where 30bps becomes unprofitable |
| P1-F02 no-candle control | E and B/C/D-vs-E, D-vs-B absent | Intercept-only train-history E; B/C/D separately compared against both A and E, D vs B; 12 family endpoints including 5 absolute nets |
| P1-F03 statistical error | Missing promotion gate, absolute net CI | 24-observation paired moving-block bootstrap; simultaneous Bonferroni familywise percentile intervals (no unsupported uncentered p-value); require absolute and paired lower >0 at stress cost, and E control |
| P1-F04 custody admission | No custody module; unchecked caller flag | `custody_verified=True` rejected, only recomputed `VerifiedCustodyAttestation` with independent pinned original SHA256 source/label trust registry and original price/identity/feature linkage; committed registry EMPTY/UNAPPROVED; synthetic/caller-controlled files cannot authorize candidate on this branch |
| P1-F05 UTC hourly selection | No coverage report | `:00/:15/:30/:45` eligibility and exclusion by side/regime; decision fixed preregistered UTC HOURLY_ONLY with existing 16 closed bars; alternate prospective 19-bar collection requires new preregistration. Never reconstruct historical missing bars. |
| P2-F06 temporal dependence | Missing non-overlap horizon count | Nonoverlapping 3600s label windows counted, conservative positive-ACF N-effective diagnostic; 4/8/24/48 block-length sensitivity; nominal N not misrepresented as iid. |

## Synthetic fixture — falsification aid, NOT PnL evidence

Fixed synthetic 70 hourly rows, train-min 20, 2 purged folds, OOS N=48, 15bps, labels constructed to reward one geometry partition (not a trading dataset):

| Policy | Mean net bps per OOS opportunity |
| --- | ---: |
| A — no abstention | -8.3333 |
| E — train-only no-candle abstention | 0.0000 |
| B — geometry | +28.3333 |
| C — patterns | 0.0000 |
| D — combined | +28.3333 |

B-E = +28.3333bps, C-E = 0, D-E = +28.3333bps, D-B = 0. Identical action decisions under 15bps and 30bps. **Synthetic labels were deliberately constructed**, so every one of these numbers is *only* a unit test of comparison semantics; the exercise DOES NOT establish predictive validity, interval coverage or economic EDGE. Primary test fixture additionally confirms positive E actions can become unprofitable after repricing at 30bps without retraining.

## Scientific blockers

Original T0 source bytes, trusted pre-outcome immutable hashes, matching 1h labels, real rejected-row counts, cost/fill attestations, and approved AUD custody anchors remain missing. No real candidate profits, win rate, original feature event frequencies, CIs, alpha, PnL or actual sample attrition have been asserted.

Safety: ZERO_SPEND=HARD, PAPER_ONLY=true, LIVE=false, REAL_ORDERS=0, CAPITAL=0. ORDER100 and production core untouched; draft PR #198; no merge/deploy.
