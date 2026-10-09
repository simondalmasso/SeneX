# M17 ARENA INDEPENDENT — 02: NUEVA EVIDENCIA Y ALTERNATIVAS GRATUITAS (INV-2)
Todas las vías listadas fueron PROBADAS HOY salvo donde se marca lo contrario. Sin credenciales, sin pagos, sin feeds autenticados, sin captura prospectiva nueva (solo GETs históricos acotados).

## Clasificación estricta en tres categorías

### A) EVIDENCIA CAPAZ DE CERTIFICAR EL EVENTO (label oficial)
| Vía | Estado probe | Qué certifica | Límite |
|---|---|---|---|
| **Polygon `eth_getLogs` ConditionResolution** (CTF `0x4D97...6045`, topic0 `0xb44d...9894`, filtro por conditionId u oracle) | **VERIFICADO HOY**: tenderly public gateway OK; 96 resoluciones cosechadas en 1 barrido; match al segundo con Gamma | Outcome oficial + timestamp de bloque + resolutor, inmutable | NO certifica el input TWAP (01/E2); autoridad = clave Polymarket |
| Gamma API `events?slug=btc-updown-5m-<ts>` | VERIFICADO HOY (2 mercados) | conditionId, tokenIds, rule bytes, outcome declarado | Provider-origin, mutable en teoría ⇒ sellar con hash al capturar y cruzar contra on-chain |
| RPCs públicos alternativos | PROBADOS HOY: tenderly OK; publicnode OK para bloques pero 403 en getLogs; polygon-rpc 401; 1rpc 503; blastapi muerto; blockpi/omniatech 521; llamarpc DNS† | — | La vía existe pero depende de gateways gratuitos volátiles; mitigación: múltiples gateways + Dune/Goldsky como respaldo (no probados hoy) |

### B) DATOS AUXILIARES ÚTILES (investigación, no certificación)
| Vía | Estado probe | Uso |
|---|---|---|
| **CLOB `prices-history`** para mercados 5m CERRADOS | **VERIFICADO HOY**: 23 puntos (~1/min) para el mercado testigo ya resuelto, sin auth | Cuota-proxy T0−δ retrospectiva: precio último/mid por token; SIN profundidad, SIN book ⇒ todo PnL derivado es `FILL_UNVERIFIED`, cota optimista |
| **data.binance.vision** (bulk klines) | **VERIFICADO HOY**: HTTP 200, zip 70KB de 1m klines — nota operativa: la CDN responde desde este sandbox aunque api.binance.com esté geo-bloqueada | Serie de referencia exchange para FALS-2 (divergencia label-vs-exchange); jamás label de settlement |
| OKX history-candles / Kraken OHLC | Verificados en sesiones previas (KS-1/KS-3), $0 | Ídem; triangulación multi-venue |
| RTDS legacy `crypto_prices_twap_sixty` | NO re-probado por mí (evité WS nuevo bajo NO_PROSPECTIVE_COHORT); AUD ya lo smoke-verificó públicamente | ÚNICA vía $0 de capturar TWAP60 forward-only si el owner autoriza captura prospectiva sellada |
| `data-api.polymarket.com` (trades) | NO PROBADO HOY | Tamaños de trade reales cerca del cierre → falsador de capacidad; queda UNKNOWN |

### C) PROXIES QUE NO PERMITEN PROBAR EDGE NETO (prohibido promover)
- Candles Binance/OKX como label (la regla oficial es TWAP60 Chainlink con tie⇒UP; un close/open de exchange es proxy con divergencia no nula — medirla es FALS-2, usarla como label es fraude metodológico).
- Feed Chainlink genérico `crypto_prices_chainlink` (≠ referencia de settlement; confirmado por AUD P1b, no lo discuto).
- Gamma `outcomePrices` sin cruce on-chain (declarativo).
- Equity curves de vendors (ver trader.dev abajo).

## trader.dev MCP — verificación propia (documentary-only, sin auth, sin créditos usados)
Fetch directo de `https://mcp-api.trader.dev/pricing` hoy:
- Free tier "$0 forever": acceso MCP, backtesting, **"Limited monthly Quidi credits" SIN monto público**. Menciona rollover semanal y "2× créditos permanente" por verificar una cuenta de exchange. 1 crédito = 1 acción MCP; "most strategies need 10-30 credits".
- **`1000_FREE_CREDITS_PER_DAY` = NO VERIFICADO** (coincido con AUD #208; mi fetch independiente tampoco encuentra ese número; la unidad temporal publicada es mensual/semanal, no diaria).
- Exchanges citados: Bybit/BloFin/Toobit/WeeX. **Cero semántica Polymarket** (condition/CTF/TWAP60/fees por mercado) en el material público.
- Clasificación: `SECONDARY_OFFLINE_BACKTEST_CANDIDATE_NOT_ORACLE_SOURCE` — confirmo la de AUD. Para M17 su valor marginal es ~nulo: lo que ofrece (backtests OHLC exchange) ya lo tenemos gratis y auditable con data.binance.vision + motor propio. No gastar créditos ni atención ahí.

## La síntesis que M17 no tenía
**Corpus retrospectivo económicamente útil = (A) labels on-chain + (B) prices-history pre-cierre + fees por mercado de Gamma/CLOB.** Las tres patas son públicas, $0, históricas (no prospectivas) y están verificadas hoy. Alcanza para FALSAR (matar hipótesis con cotas optimistas) aunque no para CERTIFICAR edge positivo (fills no identificables). Esa asimetría es exactamente la correcta para el estadio actual: EDGE=UNPROVEN se ataca por falsación barata, no por confirmación cara.

† Detalle de fallas RPC registrado para no re-probar: publicnode getLogs=403; polygon-rpc.com=401; 1rpc.io/matic=503; blastapi=deprecado; blockpi/omniatech=521; meowrpc/llamarpc=DNS; ankr=requiere key; zan.top=getBlock inestable; drpc=400 en getLogs público.