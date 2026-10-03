# ORDER096 — Indicator Registry

STATUS=OFFLINE_RESEARCH_ONLY
EDGE=UNPROVEN
DECISION_PATH_IMPORTS=FORBIDDEN
PAID_PURCHASES=0

This registry separates mathematical families from branded TradingView scripts. Popularity is not evidence. A candidate only survives if it contributes out-of-sample information beyond simpler baselines under the same timestamps and costs.

| Candidate | Access / reproducibility | Primary information family | SENEX overlap hypothesis | ORDER096 disposition |
|---|---|---|---|---|
| Squeeze Momentum [LazyBear] | Public/open-source TradingView script | volatility compression + linear-regression momentum | MEDIUM-HIGH: SENEX already has volatility and price momentum, but compression/release state is distinct | IMPLEMENT baseline + sparse release event |
| UT Bot Alerts [QuantNomad] | Public/open-source | ATR trailing stop / trend state | HIGH: overlaps ATR, trend/regime and momentum | IMPLEMENT representative ATR-trailing baseline |
| SuperTrend [KivancOzbilgic] | Public/open-source | ATR trailing stop / trend state | HIGH | IMPLEMENT representative ATR-trailing baseline |
| Chandelier Exit [everget] | Public/open-source | ATR trailing exit / trend persistence | HIGH | IMPLEMENT representative ATR-trailing baseline |
| HalfTrend [everget] | Public/open-source | ATR/trend state | HIGH | HOLD until first ATR cluster screen; do not multiply near-duplicate trials yet |
| ZLSMA [veryfid] | Public/open-source | low-lag linear-regression trend | MEDIUM-HIGH: overlaps price momentum / 4h trend | IMPLEMENT |
| WaveTrend [LazyBear family] | Public/open-source variants | normalized oscillator / short-cycle momentum | MEDIUM: potentially different phase information | IMPLEMENT |
| VWAP + EMA9/21 Pullback [jagkum] | Public/open-source | volume-weighted location + trend + pullback | MEDIUM: SENEX has volume and momentum but not an explicit session VWAP anchor | IMPLEMENT bias first; pullback trigger later only if bias survives |
| ML: Lorentzian Classification [jdehorty] | Public/open-source, complex | approximate-nearest-neighbor classifier over RSI/WT/CCI/ADX-style features + filters | HIGH model-degree risk; mixes several common TA families | PHASE 2 only after simple baselines; freeze exact config before OOS |
| LuxAlgo Signals & Overlays | Invite-only / normally paid | multi-feature proprietary toolkit | UNKNOWN and not reproducible from public implementation | EXCLUDE from implementation; no payment under ORDER096 |
| LuxAlgo Oscillator Matrix | Invite-only / normally paid | proprietary oscillator ensemble | UNKNOWN | EXCLUDE from implementation |
| LuxAlgo Price Action Concepts / SMC | Invite-only / normally paid | market structure / order-block / liquidity concepts | potentially distinct, but implementation opaque | OBSERVABLE comparison only if owner already has lawful access; no reverse engineering |

## Family compression

ORDER096 treats the candidates as a small number of hypotheses, not twelve independent opportunities.

### ATR / trailing-trend cluster

- UT Bot
- SuperTrend
- Chandelier Exit
- HalfTrend

These all consume closely related price-range / ATR / trailing-state information. Testing all four as independent “discoveries” would inflate researcher degrees of freedom. The first screen therefore uses UT Bot, SuperTrend and Chandelier as representatives. HalfTrend is admitted only if the representatives show materially different behavior or one survives OOS.

### Trend / momentum-smoother cluster

- ZLSMA
- Squeeze Momentum directional histogram
- WaveTrend
- existing SENEX price momentum / 4h regime

The scientific question is not which chart looks better; it is whether any candidate supplies information when SENEX's simpler momentum/regime view is wrong or uncertain.

### Volume-location cluster

- UTC-session VWAP + EMA9/21 bias
- later: exact pullback/reclaim event

This is the strongest candidate for genuinely different information among the simple scripts because price relative to an accumulated volume-weighted anchor is not identical to one-bar price momentum. The reset convention is explicit: UTC day for 24/7 crypto. Any other anchoring rule becomes a separate preregistered hypothesis.

### Complex classifier cluster

Lorentzian Classification is intentionally delayed. It has more features, filters and neighbor-selection choices than the simple baselines and therefore a much larger overfit surface. It cannot be allowed to “win” after the simpler family results are seen and then be tuned on the same holdout.

## Source references

- LazyBear Squeeze Momentum: https://www.tradingview.com/script/nqQ1DT5a-Squeeze-Momentum-Indicator-LazyBear/
- QuantNomad UT Bot: https://www.tradingview.com/script/n8ss8BID-UT-Bot-Alerts/
- KivancOzbilgic SuperTrend: https://www.tradingview.com/script/r6dAP7yi/
- jdehorty Lorentzian Classification: https://www.tradingview.com/script/WhBzgfDu-Machine-Learning-Lorentzian-Classification/
- everget Chandelier Exit: https://www.tradingview.com/script/AqXxNS7j-Chandelier-Exit-everget/
- veryfid ZLSMA: https://www.tradingview.com/script/3LGnSrQN-ZLSMA-Zero-Lag-LSMA/
- everget HalfTrend: https://www.tradingview.com/script/U1SJ8ubc-HalfTrend/
- WaveTrend family reference: https://www.tradingview.com/script/cYG1lqRI-WaveTrend-LazyBear-vX-by-DGT/
- jagkum VWAP + EMA9/21 Pullback: https://www.tradingview.com/script/Zdz7QFNM-VWAP-EMA9-EMA21-Pullback-Scalper/
- LuxAlgo Signals & Overlays: https://www.tradingview.com/script/fYHlrAoz-LuxAlgo-Signals-Overlays/
- LuxAlgo Oscillator Matrix: https://www.tradingview.com/script/S8svsT4N-LuxAlgo-Oscillator-Matrix/
- LuxAlgo Price Action Concepts: https://www.tradingview.com/script/ZGl2xWym-LuxAlgo-Price-Action-Concepts/

No Pine source is copied into SENEX by ORDER096. Implemented baselines are independent mathematical reimplementations of the public concepts for research comparison.


## Second-wave HTF / context candidates

| Candidate | Distinct information? | Minimal ORDER096 representation | Current disposition |
|---|---|---|---|
| HTF Fractal Bars [Herman] | LOW-MEDIUM; mostly visual HTF OHLC/structure, overlaps existing 4h regime | none | visual/reference only |
| Premium & Discount Map [Herman] | MEDIUM; explicit location inside prior HTF range is not currently a SENEX feature | `htf_discount_reversion_4h` | tested; no promotion |
| Tech Leadership Map+ [Herman] | distinct but cross-asset US-equity context | none | reject for current BTC mission |
| HTF Liquidity Map [Herman] | MEDIUM; completed HTF high/low levels not currently explicit | prior completed 4h high/low | tested through sweep baseline |
| Sweep Reversal Map+ [Herman] | MEDIUM; price-level sweep/reclaim event not currently explicit | `htf_sweep_reclaim_4h` | tested; no promotion |
| Minicharts Pro+ [Herman] | visualization only | none | no model input |

The two implemented HTF baselines intentionally compress three related scripts into two falsifiable primitives. This avoids feature multiplication.


## User-supplied iFVG Ultimate+ Deluxe source

Source: Google Doc `Pastebin INFO SENEX`, read directly on 2026-10-03.

This is treated as a confluence bundle rather than one indicator. Its unique-enough components are tracked as separate hypotheses:

| Component | Redundancy | Disposition |
|---|---|---|
| IFVG inversion | LOW-MEDIUM | KEEP research hypothesis; no production feature |
| confirmed sweep/reclaim | MEDIUM | KEEP research hypothesis; compare to completed-HTF sweep baseline |
| BTC/ETH SMT divergence | LOW-MEDIUM | KEEP research hypothesis; requires aligned dual-asset T0 data |
| lower-TF delta proxy | HIGH | REJECT duplicate of SENEX delta/orderflow family |
| session/macro windows | HIGH | REJECT duplicate of SENEX session/regime enrichment |
| volume profile/value area | MEDIUM complexity | HOLD; no evidence it removes current uncertainty |
| six-check grade | combination only | REJECT until components independently earn evidence |

See `IFVG_SOURCE_AUDIT.md` for causality notes and the small exploratory proxy screen.
