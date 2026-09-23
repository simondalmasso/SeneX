# ARQ2-STRATEGY-RADAR-005

Status: **COMPLETE**
Mode: **EXTERNAL_RESEARCH_ONLY**
Base: `eb7c76b92118828c46f692c06bb9ba234db8e3e7`

## Decision

**REPLACE_SENEX_RAW_SCORE_NOW = NO_KEEP_FROZEN**

The external literature is useful enough to define isolated hypotheses, but it does not justify replacing SENEX's raw 1h architecture. The strongest trend results operate at 20d/30d+ horizons; perp-factor evidence is mostly multi-asset/cross-sectional; semivolatility evidence does not yet establish a 1h transfer contract; and options/basis strategies are different sleeves rather than directional BTC alpha. ORDER-004 is explicitly not used as a strategy-selection result because it is a small, outcome-conditioned settled sample.

## Source saturation

Twenty qualified primary records were retained; seventeen are dated inside the last 90 days. The scan covered all ten required families. Source saturation was reached after three defensible research hypotheses emerged and additional fresh sources mostly reinforced the same family-level distinction: **signal vs regime/risk-control vs separate sleeve**.

Named strategist/manager evidence includes Colin Basco (Coinbase Institutional), Grant Fisher (Meli Vora Capital), Rupam Shrivastava (Frontiers Capital), and Albert Ventura-Traveset (Iqana). Their commentary is not treated as authority when no reproducible rule is disclosed.

## Family conclusions

1. **Multi-horizon trend / TSMOM — 13/16, ADAPTABLE as regime/risk control.** Strongest strategy evidence, including explicit walk-forward/cost sensitivity, but not a 1h transplant.
2. **Funding + perp basis — 12/16, ADAPTABLE as regime feature.** Existing SENEX fields make it cheap to falsify; external evidence is not direct BTC 1h alpha.
3. **Order-flow/CVD/price-volume — 11/16, ADAPTABLE direct research.** Intraday-compatible and already represented in SENEX, but high-turnover cost/OOS rigor is insufficient.
4. **On-chain shallow heuristics — 11/16, ADAPTABLE with bounded extension.** Glassnode's methodology is unusually disciplined (walk-forward, 20% OOS, live window, slippage, sensitivity), but no exact candidate rule/data contract is free and current SENEX lacks most on-chain inputs.
5. **Semivolatility timing — 10/16, ADAPTABLE risk control.** BTC-specific fresh evidence is interesting, but exact 1h timing/cost/OOS transfer is unresolved.
6. **Liquidation instability — 10/16, ADAPTABLE risk monitor.** Held-out event-history result is credible but daily/multi-asset and requires liquidation data SENEX does not currently freeze.
7. **Range/chop / mean reversion — 9/16.** Timely manager evidence and cross-sectional reversal evidence exist, but no public exact BTC-only 1h costed rule qualifies for TOP3.
8. **Basis/carry — 9/16, SEPARATE SLEEVE.** Coherent and institutionally used, but categorically not SENEX directional alpha.
9. **Options VRP — 6/16, SEPARATE SLEEVE / regime only.** Fresh evidence is strong for positioning and volatility state, not for a reproducible SENEX rule.
10. **Macro/risk-beta filters — 5/16, regime only.** Useful explanatory context; not enough systematic strategy evidence. Fresh attention research supplies a valuable null control rather than alpha.

## TOP3 research candidates

### 1. MULTI_HORIZON_TREND_AGREEMENT_AS_REGIME_FEATURE — 13/16

**Why it survives:** Coinbase reports monotonic 20d forward returns as 30/90/365d trend agreement rises; Howden–Andreev add a fixed 30d walk-forward test and 25–50bps cost sensitivity.

**Why it might fail:** horizon transfer. A 20d/30d result can disappear completely at 1h.

**SENEX already has:** BTC price history and the frozen 1h candidate/outcome path.

**Missing:** no new market feed; only enough historical warm-up.

**Minimum atomic test:** add no trading logic. Preregister the 0/1/2/3 trend-agreement state and stratify future all-opportunity frozen SENEX 1h score behavior by state.

**Primary metric:** prospective frozen-score discrimination/calibration by state.

**Falsifier:** no ordered/stable difference out of sample or sign reversal.

**Required horizon/N:** 365d warm-up; then every prospective 1h opportunity. No numeric promotion N is invented because the external sources do not justify one for 1h.

### 2. PERP_MICROSTRUCTURE_STATE_FUNDING_OI_ORDERFLOW — 12/16

**Why it survives:** fresh Glassnode evidence shows CVD/funding/OI states reversing materially week-to-week; perp-return papers repeatedly identify basis, funding and price-volume drivers; SENEX already records related inputs.

**Why it might fail:** cross-sectional/horizon mismatch, multiple testing, and turnover costs.

**SENEX already has:** `funding_signal`, `oi_momentum`, `orderflow`, `bidask_imbalance`, `volume_delta`, `price_momentum`.

**Missing:** canonical venue-consistent CVD if tested separately and a conservative fee/slippage contract before any trading claim.

**Minimum atomic test:** with all transforms frozen, test each raw field separately on a chronological 1h holdout. No combinations, thresholds or feature selection on the first pass.

**Primary metric:** holdout ROC-AUC/Spearman association plus turnover/cost accounting.

**Falsifier:** null/sign-unstable holdout behavior or disappearance after conservative costs.

**Required horizon/N:** prospective 1h all-opportunity holdout; no unsupported numeric promotion N is invented.

### 3. SEMIVOLATILITY_STATE_AS_RISK_CONTROL — 10/16

**Why it survives:** fresh BTC-specific work argues that upside and downside volatility should not be treated symmetrically; this is directly relevant to risk-control architecture and does not require changing direction.

**Why it might fail:** accessible evidence does not prove 15m/1h transfer, costs or explicit OOS.

**SENEX already has:** raw price/return history from which semivolatility could later be derived in an isolated research path.

**Missing:** exact paper/code lookback and target semantics; no frozen semivolatility field exists today.

**Minimum atomic test:** first recover exact source timing. Only then compare unchanged SENEX decisions across pre-decision semivolatility states in PAPER research.

**Primary metric:** OOS variance/drawdown reduction with directional candidate unchanged.

**Falsifier:** no OOS risk reduction or benefit requires post-hoc threshold/horizon tuning.

**Required horizon/N:** unresolved from accessible source; no 1h N is fabricated.

## Current regime layer

As of the latest sources, BTC moved from clear range/chop (Sep 2–8), through a shallow downside break (Sep 16), into a >10% rebound touching ~$86k by Sep 21. Spot and perp taker flow flipped from aggressive selling to buying; OI and funding were elevated; the rally included short liquidations. The volatility relationship also reversed during September: Coinbase saw a large positive VRP on Sep 3 while Glassnode later reported implied below realized. That instability is a reason to test regime-sensitive hypotheses, not a reason to fit a threshold today.

## Hard exclusions

- No daily/20d/30d result is relabeled as direct 1h alpha.
- Basis/carry and options VRP remain separate-sleeve concepts.
- Market commentary without a reproducible rule cannot enter TOP3.
- No ORDER-004 parameter selection.
- No calibration fitting, feature selection, threshold tuning, CORE/model mutation, deploy, LIVE, order or capital action.

EDGE remains **UNPROVEN**.
