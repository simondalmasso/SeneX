# 04 — Dictamen final: parar, fixtures o decision del owner (Q1–Q5)

**ORDER:** M17-GLM-P4-SOURCE-DECIDABILITY (#208) · **Fecha:** 2026-10-09 · **Modo:** DOCUMENTATION_AND_EXISTING_FILES_READ_ONLY, ZERO_SPEND, PAPER_ONLY

## 1. ¿Que evidencia decisiva existe?

1. **Cuatro experimentos sinteticos reproducidos numericamente por AUD** (K2 causalidad temporal, K3 quote≠fill, K5 Brier⇏edge, K6 multiplicidad sin ledger; #206 comentarios 11:35Z y 11:43Z, byte-exactos sobre scripts/CSVs del paquete P3). Son decisivos para una cosa y solo una cosa: **la no-universalidad de las garantias de implementacion** — no toda cita es fill, no toda mejora de calibracion es edge, no toda significancia sin registro es significancia, no todo T0 reconstruido es causal. Ninguno afirma un defecto historico real de SENEX.
2. **Refutaciones documentales** (P3, con fuentes oficiales): el relay del proveedor no es un reporte firmado de Chainlink; E18 y Decimal no son intercambiables; los rebates maker no estan garantizados (pro-rata, discrecionales, minimo $1).
3. **K4 con generador original aportado y auto-reproducido byte-exacto** en esta ejecucion (doc 03 §5), a disposicion de AUD.
4. **La cadena documental completa de la clase de mercado** (regla→fuente→ventana→resolucion→autoridad) con estados de disponibilidad verificables campo a campo (matriz 00, filas R1–R14).
5. La refutacion canonica #139 (GD beta = 0.4346033993273502 vs IRLS = 0.43460339963922673) permanece cerrada; no se reabre nada historico.

## 2. ¿Que evidencia imprescindible falta?

El artefacto unico que bloquea toda la evaluacion cientifica del edge neto BTC5m: **la serie TWAP60 original de la fuente nombrada para las ventanas historicas** (bytes RTDS legado / wire PolyBolt / reportes firmados Chainlink, con relojes de evento y recepcion). De ella cuelgan la verificacion de equivalencia de etiquetas (L3→L4), y con ella la legitimidad de cualquier metrica historica. En cascada faltan ademas: receipts T0 originales (libro por token, fees selladas, doble reloj), la senal SENEX nativa de 300 s congelada en T0 (nunca existio; prohibido recalcular), fills verificados propios (`REAL_VERIFIED_FILLS=0`) y el ledger de intentos del pasado (no reconstruible). El detalle por capa con estados STOP esta en el doc 02.

## 3. ¿Cual es la forma mas economica y valida de obtener una respuesta cientifica, sin ejecutar capturas no autorizadas?

**Ninguna simulacion adicional puede cambiar el estado de decidibilidad** — esa es la conclusion economica central de P4: el wall ya no esta en el calculo sino en la custodia de datos, y mas GLM-batches de toys serian variantes no registradas de optimizacion, exactamente lo que #208 prohibe. Las tres unicas acciones con valor cientifico, todas sin capturas no autorizadas:

1. **STOP investigativo** (coste cero): aceptar `NOT_IDENTIFIABLE` para la pregunta historica y cerrar la linea GLM sin crear P5.
2. **OFFLINE_FIXTURE_TESTABLE** (coste casi cero, via ARQ, ya secuenciado por #205): los tres RED nuevos (sustitucion de fuente, propagacion de clase de evidencia, guard de limite de autorizacion) + D1–D4 endurecen el fail-closed para que la futura evidencia, si algun dia existe, no pueda falsearse por las rutas que P3 cuantifico.
3. **EVIDENCE_REQUEST** (coste cero para GLM, decision exclusiva del owner): el limite de autorizacion del doc 02 §5 — captura prospectiva sellada paper-only o acceso autenticado a archivo historico verificado — es la unica via que puede convertir la pregunta en decidible. GLM la documenta y no la ejecuta ni la solicita activamente.

## 4. ¿Que hipotesis pueden refutarse y cuales simplemente no son identificables?

| Afirmacion / hipotesis | Veredicto (#208) | Base |
|---|---|---|
| "El relay del proveedor constituye reporte firmado / autoridad de etiqueta" | `PROVEN_FALSE_FOR_THIS_LABEL_CHAIN` | documental oficial (R5–R7); cualquier cadena de etiquetas que reclame firma desde el relay es falsa |
| "E18 y Decimal son intercambiables" | `PROVEN_FALSE_FOR_THIS_LABEL_CHAIN` | documental oficial (guia de migracion, R6) |
| "Cita observada ⇒ fill / PnL realizable" (garantia incondicional) | `ENGINEERING_FAIL_CLOSED_ONLY` | K3 reproducido; el pipeline debe degradar a FILL_UNVERIFIED (RED-03) |
| "Mejorar Brier/log-loss ⇒ edge neto" (garantia incondicional) | `ENGINEERING_FAIL_CLOSED_ONLY` | K5 reproducido (contraejemplo constructivo, caveat de re-ajuste en doc 03 §4) |
| "Significancia sin ledger completo de intentos" | `ENGINEERING_FAIL_CLOSED_ONLY` | K6 reproducido con etiquetas corregidas (max-t RC-style / toy gaussiano, NO SPA/DSR completos) |
| "T0 retro-reconstruido es causal" | `ENGINEERING_FAIL_CLOSED_ONLY` | K2 reproducido; contrato RED-02 |
| Equivalencia historica etiqueta↔regla/fuente/ventana | `NOT_IDENTIFIABLE_FROM_AVAILABLE_ORIGINALS` | doc 01; matriz R8 |
| Edge neto historico ejecutable de M17 (H1) | `NOT_IDENTIFIABLE_FROM_AVAILABLE_ORIGINALS` | DAG L3–L8 congelado (doc 02) |
| Ledger de busqueda pasada / significancia retrospectiva | `NOT_IDENTIFIABLE_FROM_AVAILABLE_ORIGINALS` | no reconstruible; solo prospectivo corregible |
| **Hipotesis economica M17 en si (edge neto OOS prospectivo)** | `OPEN_HYPOTHESIS` | ni confirmada ni refutada:EDGE=UNPROVEN; solo evidencia OOS prospectiva genuina y neto-negativa contra un umbral precomprometido podria falsificarla (camino (d) del doc 02 §4) |

**Regla respetada en toda la tabla:** nunca se etiqueta "edge refutado" por mera ausencia de evidencia (correccion obligatoria de #208 §0 y AUD #206).

## 5. ¿Conviene detener aqui, continuar solo con fixtures offline o pedir al owner una decision sobre fuentes?

**Recomendacion terminal (finita, sin auto-extension):**

```text
GLM_INVESTIGACION = STOP_NOW            (no P5; ninguna otra tanda sintetica cambia la decidibilidad)
ARQ_ENGINEERING   = OFFLINE_FIXTURE_TESTABLE  (RED-06/07/08 + D1-D4 segun secuencia ya fijada por #205)
OWNER_DECISION    = EVIDENCE_REQUEST    (limite de autorizacion del doc 02 §5, solo si el owner quiere decidibilidad futura)
```

Justificacion: P4 cerro la pregunta que le fue encomendada — que falta y que es resoluble — y la respuesta es que **todo lo resoluble sin datos nuevos ya esta hecho o especificado**, y lo unico resoluble con datos nuevos exige una decision del owner que no puede inferirse del exito de este paquete. La disciplina cientifica exige ahora parar: cualquier iteracion adicional de GLM sin nueva evidencia seria optimizacion no registrada sobre un espacio ya barrido, y seria precisamente el defecto K6 que este programa documentо. No se crea P5 automaticamente.

## 6. Estado final

```text
ORDER=M17-GLM-P4-SOURCE-DECIDABILITY
RESEARCH=DOCUMENTATION_AND_EXISTING_FILES_READ_ONLY   (cumplido)
ZERO_SPEND=HARD (cumplido: 0 suscripciones, 0 APIs de pago)
NO_EXTERNAL_FEED_COLLECTION=true (cumplido: solo documentacion y GitHub publicos)
NO_NEW_PROVIDER_AUTH / NO_NEW_CREDENTIALS_OR_SIGNER / NO_NEW_PROSPECTIVE_COHORT  (cumplido)
PAPER_ONLY=true / LIVE=false / REAL_ORDERS=0 / CAPITAL=0  (cumplido)
NO_MERGE / NO_DEPLOY / H011_NO_TOUCH / ORDER100_NO_TOUCH / ORDER197_NO_TOUCH / PR204_NO_TOUCH  (cumplido)
SOURCE_ADMISSIBLE=NO       (sin cambio)
COHORT_AUTHORIZED=NO       (sin cambio)
ECONOMIC_EDGE_PROVEN=NO    (sin cambio)
EDGE=UNPROVEN              (sin cambio; y ahora con la indecidibilidad historica formalmente establecida)
HISTORICAL_SETTLEMENT_REPLAY=NOT_IDENTIFIABLE
K4=GENERATOR_EXISTS_GLM_SELF_REPRO_BYTE_EXACT
K6_LABELS=RC_STYLE_MAX_T_APPROX + GAUSSIAN_EXPECTED_MAX_TOY (NO SPA/DSR completos)
P5=NO_AUTOMATIC (orden cumplida)
```
