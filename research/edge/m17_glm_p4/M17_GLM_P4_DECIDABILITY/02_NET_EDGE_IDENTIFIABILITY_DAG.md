# 02 — Identificabilidad del edge neto: DAG de prerrequisitos y tabla STOP (Q2)

**ORDER:** M17-GLM-P4-SOURCE-DECIDABILITY (#208) · **Fecha:** 2026-10-09 · **Modo:** offline, documental, sin auth

## 1. Pregunta exacta

Suponiendo que las etiquetas originales pudieran verificarse, ¿el resto de la ciencia del edge neto seria identificable? La respuesta es un DAG de prerrequisitos con estados de parada (STOP) por capa. Cuatro estados posibles por dependencia, conforme a #208: `TESTABLE_OFFLINE_NOW` (verificable hoy con fixtures), `TESTABLE_ONLY_IF_ORIGINAL_EVIDENCE_EXISTS` (verificable solo si existe evidencia original), `NOT_IDENTIFIABLE` (indecidible con el material disponible), `REQUIRES_SEPARATE_OWNER_AUTHORIZATION` (exige decision separada del owner).

## 2. El DAG (texto)

```text
L1  Regla por mercado (bytes Gamma publicos, version sellada por lectura)
    └─> L3  Serie TWAP60 original para la ventana [start,end]   [FALTA: R5-R8 de la matriz]
          └─> L4  Verificacion de equivalencia etiqueta<->regla/fuente/ventana
                └─> L2  Etiquetas autoritativas (hoy solo PLATFORM_TERMINAL_RESOLUTION)
                      └─> L9  Comparadores A/B/C + intercept-only sobre indices identicos
                            └─> L11 Veredicto de edge neto historico
L5  T0 causal genuino (doble reloj evento/recepcion, gaps, sin fuga futura)   [FALTA para el historico]
    └─> L7  Senal SENEX nativa 300 s congelada en T0           [NO_EXISTIO: registro M17 nunca corrio]
          └─> L9
L6  Libro por token a T0 (bid/ask/depth/seq/edad) + fees/tick/min_size por mercado
    [FALTA: no hay archivo publico de libros; fees si son publicos con deriva de version]
    └─> L8  Evidencia quote->fill (o FILL_UNVERIFIED explicito)   [REAL_VERIFIED_FILLS=0]
          └─> L9
L10 Ledger completo de intentos (pruebas y descartes)          [NO reconstruible para el pasado]
    └─> L11 Inferencia pareada consciente de la dependencia (bloques/FWER)
```

Regla de lectura: **cada capa consume lo que produce la capa de arriba**; una capa `NOT_IDENTIFIABLE` congela a todo lo que cuelga de ella para la evaluacion *historica*, sin importar que las capas de abajo sean ingenierilmente perfectas. Esa es la razon por la que el muro no esta en el codigo sino en la custodia.

## 3. Tabla STOP por dependencia

| Capa | Dependencia | Estado | Justificacion |
|---|---|---|---|
| L1 | Regla/identidad por mercado (slug, condition_id, token IDs, ventana 300 s, empate, fuente) | `TESTABLE_OFFLINE_NOW` (documental, lectura publica retrospectiva con advertencia de version) | Gamma publico (R1–R2); contrato de identidad ejercitable en fixtures (RED-01) |
| L2 | Etiquetas de settlement | `TESTABLE_ONLY_IF_ORIGINAL_EVIDENCE_EXISTS` | Hoy solo `PLATFORM_TERMINAL_RESOLUTION` (R4); autoridad criptografica ausente (R7, R10) |
| L3 | Serie TWAP60 original de la ventana | `NOT_IDENTIFIABLE` (historico) | Sin API historica publica; autenticacion requerida en moderno; legado forward-only con sunset (R5–R8) |
| L4 | Equivalencia etiqueta↔fuente/ventana/regla | `NOT_IDENTIFIABLE` (historico) | Depende de L3; `HISTORICAL_SETTLEMENT_REPLAY=NOT_IDENTIFIABLE` (doc 01) |
| L5 | T0 causal genuino (relojes evento/recepcion/monotonico) | `NOT_IDENTIFIABLE` (historico) / `TESTABLE_OFFLINE_NOW` (contrato fixture) | No existen receipts T0 originales; el contrato fail-closed si es testable (RED-02) |
| L6 | Libro por token a T0 + fees/tick/min_size | Libros: `NOT_IDENTIFIABLE` (historico). Fees: `TESTABLE_OFFLINE_NOW` (con deriva de version) | No hay archivo publico de libros (R11); fees por mercado publicos (R14) |
| L7 | Senal SENEX nativa 300 s congelada en T0 | `NOT_IDENTIFIABLE` (historico) | El registro de oportunidades M17 nunca capturo senales; prohibido el recalc post-hoc (`NO_T0_RETRO_BACKFILL`) |
| L8 | Evidencia quote→fill | `NOT_IDENTIFIABLE` (historico) / `TESTABLE_OFFLINE_NOW` (contrato fixture) | `REAL_VERIFIED_FILLS=0`; trades publicos de terceros no son fills propios (R12); el estado `FILL_UNVERIFIED` si es exigible (RED-03) |
| L9 | Comparadores A_NO_TRADE/B_MARKET_PRIOR/C_SENEX_CALIBRATED + intercept-only, indices identicos | `TESTABLE_ONLY_IF_ORIGINAL_EVIDENCE_EXISTS` | El contrato de alineacion es testable en fixture (RED-04); la evaluacion real exige L2+L5+L6+L7+L8 |
| L10 | Ledger completo de intentos | Historico: `NOT_IDENTIFIABLE`; prospectivo: `TESTABLE_OFFLINE_NOW` | El registro de lo ya buscado no se puede reconstruir; desde ahora si es discipline testable (RED-05) |
| L11 | Inferencia pareada consciente de dependencia + FWER | `TESTABLE_OFFLINE_NOW` (metodo) / validez condicionada a L10 | Metodo implementable; sin ledger su salida no es admisible (`SIGNIFICANCE_NOT_ADMISSIBLE`) |
| — | Captura prospectiva sellada / acceso autenticado a fuentes | `REQUIRES_SEPARATE_OWNER_AUTHORIZATION` | Unica ruta que puede cambiar L3/L5/L6/L7/L8; fuera del alcance de GLM (seccion 5) |

## 4. Re-evaluacion de "la forma mas rapida de matar M17"

P3 respondio "RED-ORACLE-EQUIV con datos publicos". Esa frase fue corregida dos veces por AUD (#206, #208 §0) y queda reformulada en los cuatro caminos legalmente distintos que exige #208:

- **(a) Fixture que desmiente una garantia incondicional de implementacion** — legal y ya ejecutado: K2 (no todo T0 reconstruido es causal), K3 (quote ≠ fill), K5 (mejorar Brier ⇏ edge neto), K6 (inferencia sin ledger ⇏ significancia) son contraejemplos de universalidad. **Alcance: destruye afirmaciones universales de implementacion; no toca ningun hecho historico.**
- **(b) Un mismatch original independiente que invalidaria esas etiquetas historicas exactas** — ilegal hoy: requiere L3+L2 (`NOT_IDENTIFIABLE`). Ninguna re-computacion desde proxies puede sustituirlo; sustituir la fuente es precisamente el defecto que el nuevo RED de sustitucion debe prohibir.
- **(c) Originales no disponibles que bloquean la evaluacion historica** — este es el estado vigente y el hallazgo central de P4: el bloqueo no es un resultado economico, es una **indisponibilidad de evidencia**. Declarar lo contrario (que M17 esta "refutado" por falta de datos) seria un error categorial ya corregido.
- **(d) Evidencia OOS prospectiva genuina, independiente y neto-negativa que falsifique un umbral economico precomprometido** — la unica forma de matar la hipotesis economica en si; exige captura prospectiva sellada y autorizacion del owner.

**Camino legal/alcanzable ahora sin abrir feeds:** solo (a) en fixtures, y los contratos fail-closed de (b)/(c)/(d) como especificaciones RED — nunca su ejecucion con datos. El dictamen final esta en `04`.

## 5. Limite de autorizacion propuesto (solo salida, nunca ejecutado ni solicitado por GLM)

Si el owner quisiera que la pregunta llegue a ser decidible algun dia, el limite minimo verificable seria:

1. **Alcance de datos (a eleccion del owner):** (i) captura prospectiva sellada forward-only del topico exacto (`crypto_prices_twap_sixty` legado mientras siga vivo, o PolyBolt `prices.crypto.twap` con credenciales otorgadas por el owner), mas regla por mercado sellada en T0, libro CLOB por token con seq/edad, fee params por mercado, y senal SENEX nativa 300 s congelada en T0; o (ii) acceso autenticado a un archivo historico de la fuente, si el owner verifica que existe y su retencion cubre ventanas enteras.
2. **Custodia:** receipts append-only con sha256 por evento, doble reloj (evento vs recepcion), anclaje externo de inmutabilidad, `SOURCE_ADMISSIBLE` solo tras verificacion independiente de bytes.
3. **Diseno:** politicas A/B/C/intercept congeladas antes de la captura; denominador completo con abstenciones; ledger de intentos desde el minuto cero; potencia/N pre-registrados; inferencia pareada por bloques con FWER; umbral economico minimo precomprometido antes de ver outcomes.
4. **Prohibiciones permanentes:** cero ordenes, cero capital, paper-only, sin merge/deploy, sin tocar ORDER100/ORDER197/H011/PR204.

Este limite es una **salida documental**, no una peticion: la autorizacion es decision exclusiva del owner y no puede inferirse del exito de P4 (#208 Q2). GLM no debe ejecutar ni pedir ejecucion automatizada de nada de esto.
