# 01 — Viabilidad del replay historico del oraculo (Q1)

**ORDER:** M17-GLM-P4-SOURCE-DECIDABILITY (#208) · **Fecha:** 2026-10-09 · **Modo:** DOCUMENTATION_AND_EXISTING_FILES_READ_ONLY, sin auth, sin coleccion de feeds

## 1. Pregunta exacta

¿Es la equivalencia historica del oraculo **decidible hoy, en modo lectura y sin autenticacion**? La respuesta requiere recorrer la cadena de campo por campo para la *clase* de mercado BTC Up/Down 5m (no para un mercado concreto inventado): regla original → fuente TWAP60 → ventana de liquidacion → resolucion terminal → autoridad verificable. La matriz campo a campo con estados de disponibilidad y URLs exactas esta en `00_SOURCE_RULE_ORACLE_ATTESTATION_MATRIX.csv` (filas R1–R14); este documento la lee y dictamina.

## 2. La cadena, eslabon por eslabon

**Regla original (R1–R3).** El texto de regla por mercado (asset BTC/USD, start/end de la ventana de 300 s, duracion, regla de empate, fuente nombrada) es un artefacto publico: la API Gamma sin autenticacion ya fue ejercitada por ORDER098 para mercados resueltos, y la documentacion oficial de resolucion (`docs.polymarket.com/concepts/resolution.md`, verificada en P3, sha256 `2264660d…`) fija el mecanismo de la clase: los mercados up/down se resuelven por **Chainlink TWAP** — el TWAP al inicio fija el *price to beat*, el TWAP al cierre fija el *final price*, ambos de la misma cadena, y el mercado resuelve **UP cuando el final es igual o mayor**. La config de clase (`cryptoMarketConfig={id:btc-5m-twap-60, twapEnabled:true, twapLookbackSeconds:60}`) vincula la ventana de liquidacion al TWAP de 60 s. Este eslabon es reproducible en lectura retrospectiva, con una advertencia explicita: la lectura retrospectiva sella los bytes *de hoy*, y la deriva de version de la regla entre T0 y la lectura no es verificable — por eso el sello debe registrarse como "regla leida en P4" y no como "regla vigente en T0".

**Resolucion terminal (R4).** El resultado terminal por mercado (Gamma `outcomePrices` 0/1; Data API v2 `GET /v2/resolutions`, documentado publico en la guia de migracion accedida en P4 con sha256 `96955b89…`) es publico y autentico **como resolucion de plataforma**. Es evidencia de clase `PLATFORM_TERMINAL_RESOLUTION`: necesaria para cualquier evaluacion, pero no una atestacion criptografica independiente. La prueba criptografica que *seria necesaria* para llamar autoritativa a una etiqueta de settlement es un **reporte firmado por Chainlink** (con `validFromTimestamp`/`observationsTimestamp` y firma verificable) o una atestacion on-chain vinculada al `condition_id` y a la ventana exacta del mercado. Ninguna de las dos existe hoy en custodia, y la documentacion consulta no muestra que la plataforma publique esos valores de settlement firmados (R10: `UNKNOWN`).

**Fuente TWAP60 (R5–R7).** Tres transportes documentados, ninguno intercambiable: (i) el legado RTDS `crypto_prices_twap_sixty` con `full_accuracy_value` en E18 fijo y `window_s=60` — publico, **forward-only**, sunset planificado; (ii) el wire crudo de PolyBolt `{v,channel,seq,ts,snapshot,dropped,payload}` con valor Decimal exacto — **requiere autenticacion** (apiKey/secret/passphrase via `{"op":"auth"}`); (iii) el evento normalizado del SDK `{topic,type,timestamp,payload}` — derivado, no original. La documentacion oficial (guia de migracion RTDS→PolyBolt, accedida en P4 con sha256 `c6bba4c8…`) confirma el mapeo exacto `crypto_prices_twap_sixty {btc/usd} → price.crypto.twap {btcusd, window_seconds:60}` ("60-second Chainlink TWAP") y prohibe aplicar la conversion E18 al decimal de PolyBolt. El spot generico (`crypto_prices` legado Binance; `price.crypto` moderno Chainlink) es una fuente **distinta** que la del settlement.

**Historico autentico sin autenticacion (R8–R9).** Ninguna pagina oficial consultada (indice `llms.txt`, seccion market-data completa, guias de migracion) documenta un archivo historico publico de la serie TWAP60 para ventanas pasadas. El legado publico era forward-only; el moderno exige credenciales; el "snapshot historico" de cada suscripcion acompana a la suscripcion y no constituye un archivo de ventanas pasadas. Se registran tambien los artefactos publicos que **si** existen y no deben confundirse con la fuente: trades ejecutados historicos (`GET /v2/trades`) y serie de precio del token (`GET /v2/prices-history`) — clases de artefacto distintas (fills de terceros y proxy de mercado del token), utiles como contexto de investigacion y prohibidos como sustitutos de la fuente nombrada.

## 3. Dictamen de viabilidad

```text
HISTORICAL_SETTLEMENT_REPLAY = NOT_IDENTIFIABLE
```

El recompute del settlement de mercados BTC5m resueltos desde la fuente nombrada en sus reglas — el experimento RED-ORACLE-EQUIV con mayor poder de falsacion identificado en P3 — **no es ejecutable hoy en modo lectura y sin autenticacion**, porque falta el artefacto exacto que requiere: la **serie TWAP60 original (bytes de RTDS legado, wire de PolyBolt o reportes firmados de Chainlink) para las ventanas historicas [start, end] de los mercados resueltos**, con marcas de tiempo de evento y de recepcion. Sin ella no se puede ni confirmar ni refutar la equivalencia etiqueta↔regla/fuente/ventana: el resultado honesto es `NOT_IDENTIFIABLE`, no un mismatch, no un EV negativo y no una prueba de nulidad (correccion ya fijada por AUD en #206 y reiterada en #208 §0).

**Precision sobre lo que NO esta bloqueado:** la parte *documental* del eslabon regla/resolucion (R1–R4) si es accesible sin autenticacion, y por tanto un future RED-01 puede ejercitar su contrato de identidad sobre esos bytes publicos. Lo indecidible es exclusivamente la mitad que requiere la serie de la fuente para ventanas pasadas.

**Rutas que cambiarian el estado (ninguna ejecutada, ninguna solicitada por GLM):**
1. Acceso autenticado a PolyBolt/Chainlink Data Streams con retencion historica verificada — decision exclusiva del owner (limite de autorizacion propuesto en `02`, seccion 5).
2. Captura prospectiva sellada forward-only de la fuente exacta — decision exclusiva del owner; es la unica ruta que produce T0/T1 con custodia completa sin depender de archivos ajenos.
3. Descubrimiento de una atestacion on-chain del settlement — hoy `UNKNOWN`; su verificacion exigiria lectura autorizada de los contratos especificos.

**Prohibiciones respetadas en esta investigacion:** ninguna conexion autenticada, ningun mercado nuevo recolectado, ningun snapshot vivo, ningun backfill retrospectivo, ningun proxy promovido a fuente. "No encontrado" no se equipara a "no existe" (R8, R10). La reclamacion de P3 de que `SOURCE_UNAVAILABLE` "mata" la hipotesis economica queda formalmente retirada: lo que hace es **bloquear la evaluacion retrospectiva**, y nada mas (ver `04`).

## 4. Nota de integridad sobre el corpus P3

Durante la verificacion de esta ejecucion se detecto y cerro una brecha menor de empaquetado de P3: el documento oficial de TWAP/realtime (`polydocs_08.md`, sha256 `d0743b82…`) estaba referenciado en `SOURCES_VERIFIED.json` de P3 pero no habia sido copiado a `contexto_verificado/` ni incluido en `SHA256SUMS` de P3 (residia solo en `scripts/netprobe_p3/`). En P4 la misma URL fue re-accedida (HTTP 200) y el cuerpo es **byte-identico** al sha256 registrado, con lo cual la brecha queda cerrada por re-verificacion sin mutar el paquete sellado P3.
