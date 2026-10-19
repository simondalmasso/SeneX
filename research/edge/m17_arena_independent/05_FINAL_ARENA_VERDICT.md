# M17 ARENA INDEPENDENT — 05: DICTAMEN FINAL (INV-5)
ARENA_M17_INDEPENDENT_20261009 · EDGE=UNPROVEN (sin cambios) · SOURCE_ADMISSIBLE=NO (sin cambios) · MUTATIONS=0

## 1. Qué conclusión de GLM CONFIRMO
- **No-replayabilidad histórica de los inputs TWAP60**: CONFIRMADA y REFORZADA — mi decode de la tx de resolución muestra que el reporte firmado ni siquiera viaja on-chain; la ausencia es estructural, no de cobertura de búsqueda (01/E2-E3).
- **Brier≠EV como contraejemplo lógico** (K5 en tanto teorema de existencia).
- **Relay ≠ atestación** (K1): correcto; confirmado por la vía más dura.
- La agenda de causalidad T0/relojes (K2) como requisito RED permanente.

## 2. Qué REFUTO o cuestiono (detalle en 03)
- **GLM-E1**: `HISTORICAL_REPLAY=NOT_IDENTIFIABLE` como enunciado global — REFUTADO por omisión material: labels y rule bytes históricos SON identificables hoy, $0, sin auth (evidencia primaria on-chain ejecutada; 0 menciones previas en todo M17). Enunciado correcto: `LABELS=IDENTIFIABLE / TWAP_INPUTS=NOT_IDENTIFIABLE`.
- **GLM-E2**: el kill económico de K5 no transfiere por tamaño de efecto (z=3.1 solo existe a N=500k; a n M17-real es z≈0.1).
- **GLM-E3**: la FPR familiar 0.54/46.7% presupone configs independientes; en familias reales correlacionadas sobre-estima — número no transferible (el riesgo de multiplicidad en sí queda en pie).
- **GLM-E4**: P(fill)=0.44 uniforme confunde brazos maker/taker; no es cota conservadora del brazo que pretende acotar.
- **GLM-E5**: P4 "completado" en sandbox externo sin publicar = `UNDELIVERED` según las propias reglas del programa.

## 3. Evidencia original encontrada (no registrada previamente en M17)
1. **Cadena de custodia on-chain del label**: ConditionResolution en Polygon para mercado BTC5m concreto, match al segundo con Gamma, payouts [1,0], tx/bloque/oracle identificados; adapter `0x58e1745b...`, resolver signer `0xd5cb79fb...`, entrada vía EntryPoint ERC-4337 `0x4337084d...` (evidence/*.json, reproducible por cualquiera).
2. **Negativo estructural**: calldata y eventos de la resolución NO contienen precio ni reporte Chainlink (escaneo exhaustivo E8/E18/timestamps = 0 hits) ⇒ la autoridad del label es la clave del resolver, no una verificación del cómputo.
3. **Cosechabilidad**: 96 resoluciones del mismo adapter en 66 min vía un solo barrido getLogs — corpus de labels a escala industrial.
4. **prices-history sirve mercados 5m CERRADOS** (23 pts, ~1/min, sin auth) — cuota-proxy retrospectiva disponible.
5. **data.binance.vision responde desde entorno geo-bloqueado para api.binance.com** (HTTP 200, zip klines) — pata exchange del falsador de divergencia.
6. **trader.dev pricing verificado de primera mano**: free tier mensual limitado sin monto público; "1000/día" sin sustento; cero semántica Polymarket.
7. **Latencia de resolución medida**: 85 s post-cierre (dato operativo nuevo para cualquier modelo de rotación de capital).

## 4. ¿Existe vía de evaluación económicamente válida ejecutable HOY offline?
**SÍ — para falsar; NO — para certificar.** El corpus {labels on-chain + última cuota pre-cierre + fees por mercado} permite ejecutar FALS-1/2/3 (doc 04) completamente offline, $0, sin cohort prospectivo ni feeds autenticados. Produce: kill por dominancia, techo de transferencia proxy→oficial, y masa de frontera inadjudicable — números con IC. Lo que NO puede producir: prueba de edge positivo ejecutable (fills no identificables sin book/fill evidence ⇒ cualquier PnL positivo queda `FILL_UNVERIFIED`, cota optimista). Esta asimetría es la correcta para EDGE=UNPROVEN: barato matar, caro confirmar — exactamente el orden en que se debe gastar.

## 5. Qué debería hacer ARQ a continuación — y qué abandonar
**HACER (orden de prioridad):**
1. **Agregar verificador on-chain de labels a D1-D4** (#205): cruce ConditionResolution↔Gamma por conditionId como tercera pata de custodia (la única inmutable). RED: label que no matchea on-chain ⇒ fail-closed. Es ~100 líneas sobre getLogs + los fixtures de este informe.
2. **Ejecutar FALS-2 (divergencia) primero** — es el más barato (solo labels + klines bulk) y su número decide si toda la evidencia exchange-proxy del programa es admisible o basura; después FALS-1, después FALS-3.
3. Mantener el inbox #210 como condición dura: ningún número de GLM asciende de `GLM_REPORT_ONLY_UNREPRODUCED` hasta bytes en el repo.

**ABANDONAR:**
- La recuperación de TWAP60 histórico por cualquier vía (archivada, vendor, scraping): ausencia por diseño, demostrada. Único sustituto legítimo: captura prospectiva propia sellada del RTDS público — y eso es una decisión de owner aparte, no una tarea de investigación.
- trader.dev como fuente para M17 (valor marginal ~0 sobre data.binance.vision + motor propio).
- Nuevas simulaciones sintéticas de fill/FPR sin datos reales: K3/K5/K6 ya dieron lo que podían dar (contraejemplos lógicos); más constantes sintéticas solo agregan ruido citable.

## Cierre
M17 preguntaba si el oráculo histórico está bloqueado. Respuesta precisa: **la mitad que paga (labels) está abierta de par en par y nadie la había mirado; la mitad que explica (inputs TWAP) está cerrada por diseño y nadie la va a abrir.** Con la mitad abierta alcanza para montar los tres falsadores que decidirán si BTC5m merece un solo día más de trabajo. Ninguno requiere fe, créditos, ni permiso de nadie — solo ejecutarlos antes de escribir más documentos.
