# SENEX — AUDIT 100% READ_ONLY + PLAN PARA METERLO A TRADEAR
AUDIT_TS=2026-10-04T00:35–00:50Z · MUTATIONS=0 · main=a181dc90 · H011=5e074b23 (exact=true)

═══════════════════════════════════════════
PARTE A — AUDIT REFRESH (delta desde 2026-09-28)
═══════════════════════════════════════════

## Código
- main avanzó 1 commit: **a181dc90 "fix PortfolioAnalytics double fee subtraction" (PR#100)**, RED/GREEN, CI success 23:15Z. El bug restaba fees DOS veces (journal ya es neto) ⇒ el PnL histórico se mostraba PEOR de lo real. Fix correcto y honesto. **Sin deployar** (runtime = 5e074b23, 1 commit detrás — drift menor, dirección conservadora).

## Runtime vivo (readbacks 00:35Z)
| Superficie | Valor | Lectura |
|---|---|---|
| provenance | 5e074b23 exact=true | identidad íntegra |
| safety | PAPER, orders=false, live_capital_locked=true, hard_lock v1 | locks intactos |
| readyz | 200 ready | sano, uptime 4.6 días (boot Sep-29 09:39Z) |
| score | **REJECTED**, n_indep=687, global 50.36%, Wilson-lo 0.4663 | sigue sin señal global |
| direcciones | LONG 53.7 / SHORT 48.2 | la "anomalía" se achica con n (54.85→54.03→53.70) — trayectoria típica de ruido convergiendo a base-rate (~50.9) |
| gptrader | packets=1082, takes=0, abstains=16, last_run=CANARY 28-Sep | 🔴 **TREATMENT IDLE 6 DÍAS**: 1082 packets sellados y nadie decide desde el canary |
| control PAPER | cash 9979.51, 4 cerradas; journal=7 (5 post-reset, TODAS LONG: −0.37, −8.35, −10.45, +5.57, −7.26) | 🔴 el control está "probando" el corte LONG en vivo y pierde −20.86 USD/10k ≈ **−21bps en 5 trades ≈ −4bps/trade** — coherente con KS-3 (esperado −7 a −17bps/trade según costo) |

## Hallazgos nuevos de esta pasada
1. **F-NEW-1 (MEDIUM, DESIGN_GAP)**: `LiveGate` (ACT XXV) existe y es sólido operacionalmente (6 condiciones, re-evaluación continua, auto-relock) pero sus umbrales son **pre-aritmética**: exige WR global ≥52% a horizonte 1h… donde el breakeven taker es **90.3%** (KS-3 ejecutado). Si mañana el WR tocara 53%, el gate abriría la puerta a perder ~−12bps/trade con bendición formal. El gate necesita una condición ECONÓMICA (E[PnL] bruto > costos con CI de bloques), no solo WR.
2. **F-NEW-2 (MEDIUM, DRIFT)**: experimento TREATMENT detenido — 1082 packets, 0 decisiones post-canary. Cada día idle es muestra prospectiva perdida; el experimento más caro de instrumentar está instrumentado y apagado.
3. Residuales sin movimiento: deploy-gate técnico inexistente (N9, el del incidente 27-Sep), canon AUD/ARQ fósil, image_digest estático, asimetría fees 5/10bps entre brazos, F-08 reset del control (el journal ahora mezcla 2 trades pre-reset + 5 post-reset sin marcador de época).

## Estructura 100%
Mapa completo módulo-por-módulo, invariantes y topología verificados: `SENEX_FULL_STRUCTURE_AUDIT_20260928.md` (sigue vigente; delta desde entonces = PR#100 arriba + este refresh). Resumen de capas: GitHub(main protegido, canonical-ci)→Dockerfile(non-root, HEALTHCHECK dual)→H011{web 8080 GET-only · MCP 8787 bearer+lease · reconciler repair-only}→volumen persistente{sealed packets 1082 · decision log OK}→Supabase(predicciones/settlement proof-qualified 15m/1h)→Cloudflare D1 gateway(congelado). Ciencia: score auto-rechazado, verdict engine 600h/14d + block bootstrap, T0 sin lookahead en 4 auditorías adversariales.

═══════════════════════════════════════════
PARTE B — PLAN PARA METERLO A TRADEAR
═══════════════════════════════════════════

## B.0 La verdad primero (por qué HOY sería regalar plata)

No existe hoy configuración de SENEX que gane dinero en vivo, y no es opinión — es aritmética ejecutada el 28-Sep y confirmada por el propio control esta semana:
- Breakeven a 1h con costos taker (20bps RT): **WR 90.3%**. SENEX global: 50.36%. Mejor corte (LONG): 53.7% y cayendo con n.
- Sin fricción, el mejor corte rinde +2.9bps/trade; a costos reales −7 a −17bps/trade.
- El control nativo lo está demostrando en PAPER con dinero contable: −4bps/trade promedio en sus 5 trades LONG post-reset.
**Conectar una API key hoy = pérdida esperada por trade, con certeza estadística, antes de slippage.** Por eso el plan no empieza en el exchange: empieza en el único lugar donde los números cierran.

## B.1 La única puerta económica: horizonte 24h (cuantificada, no especulada)
Con los mismos datos de mercado (87.5d OKX): breakeven @20bps = 0.903 (1h) → 0.670 (4h) → **0.475 (24h)** (med|Δ24h|=0.82%, E[|Δ|up]=1.60%). A 24h, un WR de ~50% con la asimetría up/down ya es economía positiva. SENEX **nunca midió su señal a 24h** — es la hipótesis que hay que comprar con datos antes de hablar de trading. Alternativa complementaria: ejecución maker (~0-5bps) que baja el breakeven 1h a ~52-55% — pero agrega fill-risk no modelado; solo tiene sentido si aparece señal.

## B.2 El plan — 5 fases con gates numéricos (cada flecha es falsable; fallar = volver, no avanzar)

### FASE 0 — Precondiciones (semanas 0-2, $0, sin señal nueva)
1. Deploy de a181dc90 (fix fees) vía gate normal.
2. **Reactivar GPTrader**: decisiones reales sobre los packets (está idle con 1082) — autoriza el owner vía su propio proceso de orders.
3. ORDER095 (ya especificada en ORDER094): export de las filas independientes + KS-1M/KS-2/KS-6 — cierre formal de la pregunta 1h.
4. Instrumentación 24h: agregar WINDOW_24H_S=86400 al settlement dual (la infra dual-window ya existe; delta ~30 líneas + tests RED/GREEN) + outcome_24h en audit. SIN tocar el generador.
5. Fixes de contabilidad comparable: fees simétricos 10bps ambos brazos + flag epoch_reset persistente en control.
6. **LiveGate v2**: agregar condición 7 = E[PnL_bruto] − costo_total > 0 con CI95 block-bootstrap excluyendo 0, computada del journal; y condición 8 = deploy-gate técnico verificado (main-ancestor + CI PASS). Sin esto, el gate actual es una puerta con cerradura decorativa.

### FASE 1 — Señal 24h (semanas 2-10, $0, PAPER)
- Cohorte prospectiva preregistrada: criterios, seeds y baselines (B0-B7 de COSMIC_EDGE_CALIBRATION_V1) sellados en commit ANTES de la primera fila.
- Medición primaria: **AUC de total_pressure sobre outcome_24h** + WR por dirección vs base-rate matched, CI de bloques. n objetivo: 180-200 días-muestra independientes a 24h… eso son 6+ meses — MITIGACIÓN: 24h con muestreo diario da n=56 en 8 semanas; MDE honesto a ese n ≈ 13pp ⇒ la Fase 1 solo puede detectar señal GRANDE. Si el resultado es "pequeño pero prometedor" ⇒ extender, no promover.
- GPTrader en paralelo sobre los mismos packets (pregunta incremental: ¿agrega sobre threshold? McNemar matched + reproducibilidad ≥95%).
- GATE F1→F2: AUC CI95-block > 0.5 Y WR_24h del lado operado ≥ 52% con CI que excluya el breakeven 47.5%+margen. Si no: **STOP — se declara "sin señal económica demostrable" y el plan de trading muere acá con honor.**

### FASE 2 — Economía simulada (semanas 10-16, $0)
- Escalera L1-L4 a 24h (fees→spread→slippage→latency) sobre la MISMA cohorte + una segunda cohorte réplica iniciada en paralelo.
- GATE F2→F3: PnL simulado L3 > ALWAYS_ABSTAIN y > FOLLOW_ALL con CI-block excluyendo 0, en AMBAS cohortes.

### FASE 3 — TESTNET (semanas 16-22, $0)
- La infra YA existe: `exchange_connector` con BINANCE_TESTNET_KEY/SECRET + set_sandbox_mode(True) + guards verificados por CI. 
- 4-6 semanas de órdenes testnet reales a 24h: mide fills, slippage real, rechazos, downtime operacional — nada de PnL (testnet no tiene economía real), TODO de ejecución.
- GATE F3→F4: fill-rate ≥95%, slippage real ≤ el modelado en L3, cero violaciones de riesgo, LiveGate v2 verde 4 semanas seguidas.

### FASE 4 — LIVE micro enjaulado (semana 22+, capital real)
- Capital inicial: **USD 100-300** (dinero cuya pérdida total sea irrelevante). Posición máx 2% equity. Solo el lado/corte que pasó F1-F3. Maker-first con timeout a cancel (no taker salvo salida de riesgo).
- Jaula técnica (todo ya existe o es delta chico): LiveGate v2 re-evaluado cada ciclo con auto-relock · kill-switch automático: DD>5% o últimos 20 trades WR<45% ⇒ relock + alerta · hard_paper_lock se reemplaza por live_micro_lock con cap de notional HARDCODEADO en código (cambiarlo exige PR+CI+deploy, no env) · deploy-gate técnico obligatorio (el incidente del 27-Sep con capital vivo habría ejecutado un bug conocido con plata).
- Escalado: solo duplica capital tras cada 8 semanas con PnL>0 y CI-block>0; cualquier mes negativo ⇒ vuelta al nivel anterior. Techo del programa: lo que el owner defina en una orden AUD explícita — este plan NO autoriza montos.

## B.3 Qué NO hacer (cada punto ya tiene cadáver propio)
- NO live a 1h/15m con taker: breakeven 90%/imposible (KS-3).
- NO abrir LiveGate v1 tal cual: sus umbrales aprueban estrategias perdedoras.
- NO promover por racha PAPER corta: 5 trades del control ya engañarían en ambas direcciones según la semana.
- NO agregar símbolos/features/fuentes antes del gate F1: grados de libertad gratis.
- NO deploy manual sin gate técnico: la puerta del incidente sigue sin cerrojo (N9).

## B.4 Resumen ejecutivo del plan
| Fase | Dura | Costo | Capital en riesgo | Gate de salida |
|---|---|---|---|---|
| 0 Precondiciones | 2 sem | $0 | 0 | fixes+instrumentación 24h mergeados |
| 1 Señal 24h | 8 sem | $0 | 0 | AUC>0.5 + WR≥52% CI-block |
| 2 Economía sim | 6 sem | $0 | 0 | L3 > baselines, 2 cohortes |
| 3 Testnet | 6 sem | $0 | 0 | ejecución real ≤ modelo |
| 4 Live micro | abierta | $0 infra | USD 100-300, cap 2%/pos | PnL>0 CI-block por 8 sem para escalar |

Camino más corto a la primera orden real con expectativa positiva: **~22 semanas**, y SOLO si la señal 24h existe — cosa que hoy nadie sabe, porque nunca se midió. Si no existe, el plan termina en la Fase 1 con un veredicto limpio, que vale más que cualquier cuenta fondeada: *"SENEX no tiene edge demostrable"* es un resultado válido; una cuenta en rojo diciéndolo más caro, no.