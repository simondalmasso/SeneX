# ORDER197 — verdict at code handoff

**Scientific verdict: BLOCKED_ARTIFACT_BYTES.**

The frozen, original complete 15-minute OHLCV T0 byte corpus matched to exactly 3600-second BTCUSDT reference labels was **not present/verified** in this source checkout. No authentic OOS A/B/C/D economic figures, CI95, baseline net PnL, or real pattern event counts are asserted. No computed synthetic net performance may be interpreted as financial evidence. Explicitly report N_REAL_ELIGIBLE=0_VERIFIED and T0_CUSTODY_SHA256=UNKNOWN (not proof that no historical trades exist).

Coding tests enforce required closed-bar cutoffs, no gaps/duplicates, source identities, no post-T0 Hikkake confirmations, return sign, no duplicate costs, frozen 1h target, fixed 10/15/20/30bps and purged walk-forward with train-only fitting. Any external artifactual provenance still requires an independent audit.

Recommendation to AUD: review the implementation and definition divergences from TA-Lib. Independently retrieve exact original decision-time records, hash and seal immutable files, establish one-to-one 1h outcome lineage, verify coverage and minimum evidence before running any *real* comparative test. Do not retune ORDER100, use prospective labels for tuning, merge, deploy or enable LIVE/orders/capital on this study.
