# 03 — Cierre de pruebas hostiles: RED no duplicativos, etiquetas estadisticas y K4 (Q3)

**ORDER:** M17-GLM-P4-SOURCE-DECIDABILITY (#208) · **Fecha:** 2026-10-09 · **Modo:** offline, sin tocar codigo/PRs de ARQ

## 1. Revision del espacio RED ya cubierto (no duplicar)

El espacio RED vigente para ARQ #205 es la suma de: (i) las fichas RED-01..RED-05 de P3 (`08_tests_RED_ARQ205.md`), (ii) las correcciones de AUD del comentario #6080068438 en #205, y (iii) los contratos D1–D4 de la propia #205. Cobertura actual:

| Area | Especificacion vigente | Fuente |
|---|---|---|
| Identidad oraculo (relay≠firmado, E18≠Decimal, snapshot≠update, gaps de secuencia, bytes inmutables) | D1 + RED-01 corregido | #205 §2 D1; AUD #6080068438.1 |
| Indecibilidad historica interpretada como SOURCE_BLOCKED, no como mismatch | RED-01 corregido | AUD #6080068438.1 |
| Relojes duales, backfill, gaps, fuga futura, T0 ligado a la regla congelada del mercado | D1 + RED-02 corregido | #205 §2 D1; AUD #6080068438.5 |
| Quote≠fill, NON_ATOMIC_PAIR, FILL_UNVERIFIED, sin P(fill) desde profundidad, sin PnL realizado | D2 + RED-03 corregido | #205 §2 D2; AUD #6080068438.2 |
| Fee identidad Decimal, condicional a fill, sin rebates garantizados | D3 | #205 §2 D3 |
| A/B/C + intercept-only sobre indices identicos, 300 s nativo, incrementos C-B y C-intercept, cotas inferiores conscientes de dependencia | RED-04 corregido | AUD #6080068438.3 |
| Ledger incompleto ⇒ SIGNIFICANCE_NOT_ADMISSIBLE; prohibido llamar SPA/DSR a los toys | RED-05 corregido | AUD #6080068438.4 |
| CSV malformado rechazado (12 cabeceras/11 campos), puerta de autoridad negativa, T1-only = OUTCOME_RESEARCH_ONLY | D4 | #205 §2 D4 |

Conforme a #208 Q3 se eliminan de la propuesta P3 toda expectativa que prescriba numeros sinteticos como umbrales (`P(fill)=0.44`, `FPR=0.467`) — los fixtures negativos deben fallar por estado, no por constante transplantada.

## 2. Tres RED nuevos y no duplicativos (maximo respetado)

### RED-06 · SOURCE-SUBSTITUTION REFUSAL (ataca la rama ausencia+sustitucion; D1)

**Asercion RED:** "el verificador de equivalencia de settlement acepta computar un veredicto de equivalencia/mismatch alimentado por una fuente sustituta cuando los bytes de la fuente nombrada faltan". **Datos:** fixtures con (a) bytes originales presentes (puede emitir EQUIVALENT/MISMATCH), (b) bytes ausentes + sustituto tentador (spot generico `crypto_prices_chainlink`, proxy Binance, serie `prices-history` del token, TWAP recalculado de terceros). **Comportamiento exigido:** en (b) emitir `SOURCE_BLOCKED / NOT_IDENTIFIABLE` con el artefacto faltante identificado, y **jamás** un veredicto de equivalencia ni de mismatch desde el sustituto; el estado debe propagarse a cualquier reporte derivado. **No-duplicacion:** RED-01 cubre mismatch de identidad con bytes presentes y la correccion AUD cubre la *interpretacion* del caso; ninguno especifica el contrato de maquina de la rama sustitucion. **Coste:** minimo, todo fixture.

### RED-07 · EVIDENCE-CLASS WEAKEST-LINK (propagacion de clase; D4)

**Asercion RED:** "un reporte derivado (estadistica de concordancia de etiquetas, calibracion, PnL papel) puede declararse con clase de evidencia superior a la de su insumo mas debil". **Datos:** joins de fixtures cuyos insumos mezclan clases `CHAINLINK_SIGNED_REPORT` > `PROVIDER_CHAINLINK_RELAY` > `PLATFORM_TERMINAL_RESOLUTION` > `SYNTHETIC_FIXTURE`. **Comportamiento exigido:** la clase efectiva de cualquier derivado es el minimo de sus insumos; emitir una clase superior debe ser rechazado (`EVIDENCE_CLASS_LEAK_REFUSED`), y los derivados de T1-only deben salir `OUTCOME_RESEARCH_ONLY`. **No-duplicacion:** D4 fija la etiqueta del artefacto T1-only pero no la propagacion por joins/agregaciones. **Coste:** minimo.

### RED-08 · OFFLINE AUTHORIZATION BOUNDARY GUARD (limite de alcance como contrato; D1)

**Asercion RED:** "un adaptador de proveedor del harness offline puede construirse con endpoints autenticados (SecureClient/credenciales), WS vivo o captura prospectiva sin rechazo en construccion". **Comportamiento exigido:** rechazo en tiempo de construccion (`AUTH_OR_FEED_PATH_FORBIDDEN_OFFLINE`) para cualquier configuracion con credenciales, ingestion viva o captura prospectiva dentro del modulo de investigacion M17; la unica via es una bandera de autorizacion explicita del owner ausente por defecto. **No-duplicacion:** las ordenes lo prohiben en prosa; ningun RED existente lo convierte en contrato ejecutable. **Coste:** minimo.

Los tres son offline, de estado fail-closed, sin umbrales numericos transplantados, atados a D1/D4, y no modifican codigo de ARQ (propuestas para su intake).

## 3. Correccion de nomenclatura metodologica de K6 (una pagina)

Conforme a AUD #6080053035 y a la lectura directa del script `k6_falsos_descubrimientos.py`:

1. **D2/D3 implementan un "max-t studentizado centrado con bootstrap estacionario comun por bloques, SD observado fijo" — aproximacion al estilo White RC (2000). NO implementan Hansen SPA (2005):** falta la politica de re-centrado/truncamiento especifica del SPA y su nulo dependiente de la muestra. La etiqueta "White RC / SPA" de la fila D2 del CSV de P3 queda **retirada**; la cifra 5.0%/46.7% es propiedad del estimador aproximado implementado, no de un SPA validado. Un SPA genuino seria un test separado, documentado y cruzado.
2. **D4 es un juguete de maximo gaussiano (`GAUSSIAN_EXPECTED_MAX_TOY`):** `p = 1−Φ(max_t − E[max de N N(0,1) independientes])`. NO es el DSR completo de Bailey–López de Prado, que ademas exige el error estandar del Sharpe muestral, asimetria/curtosis de los retornos y el numero efectivo de intentos dependientes. Las cifras 0%/13.33% no deben reportarse como frecuencias validadas de DSR.
3. **Incertidumbre de Monte Carlo (R_MACROS_D=60, B_BOOTS=100):** intervalos binomiales aproximados al 95%: D3 46.7% (28/60) → ~34–59%; D2 5.0% (3/60) → ~1–14%; D4b 13.3% (8/60) → ~6–25%; piso discreto del p-valor 1/101. Fases A/B/C (400 macros) y E (500 macros) llevan ±1–2 pp. Ninguna cifra es constante transferible a M17.
4. **Contradicciones internas corregidas:** fase B: el valor medido es **0.1573** (el "~19%" del comentario del CSV se retira). Fase C: teorico `1−0.95^15 = 0.5367` vs medido `0.5373` — se etiquetan teorico y empirico, no "iguales".
5. **Longitud de bloque:** L=10 ≥ solapamiento MA(5) funciona **en este fixture**; no es un teorema universal — la longitud de bloque/clustering debe elegirse por diagnosticos de dependencia pre-especificados y analisis de sensibilidad.

**Estatus K6 (sin re-ejecucion, conforme a #208):** `SYNTHETIC_NUMERIC_REPRODUCED_METHOD_LABELS_LIMITED` (AUD #6080053035). Las lecciones cualitativas (registro incompleto invalida la correccion; bootstrap i.i.d. sobre etiquetas solapadas infla falsos positivos) permanecen; las etiquetas cuantitativas quedan acotadas por esta correccion.

## 4. Caveat de seleccion de intentos en K5 (obligatorio de #208)

La construccion sintetica de K5 **fue iterada durante el desarrollo de P3**: los escenarios iniciales del toy eran rentables y la construccion se re-ajusto hasta producir el PnL neto negativo (−418.33). En consecuencia K5 es un **contraejemplo constructivo** — demuestra que "mejorar Brier/log-loss ⇒ edge neto" no es universal — y **no** una evaluacion alpha pre-registrada ni evidencia confirmatoria independiente. El caveat se hace explicito y ambas etapas (intento inicial rentable y re-ajuste) deben conservarse en el ledger de intentos. Correccion adicional de lenguaje: existe una celda de rejilla τ=0.03 con **+0.91** en 186 operaciones; el enunciado correcto es "ningun umbral produce un edge positivo materialmente robusto", no "todos los umbrales estrictamente negativos".

## 5. K4: el generador original existe y se aporta

**Hecho verificado en esta ejecucion (P4):** el paquete sellado P3 contiene `k4_edge_costes.py` (sha256 `3d906ded7674026e7e5f42708bdc916685df7d73a35ae73884a4a679e739e933`, verificado contra `SHA256SUMS` de P3 con `sha256sum -c` integral OK). AUD no lo recibio en su intake (K4=`GENERATOR_NOT_PROVIDED`, #206 11:43Z); este paquete aporta la copia **sin modificacion alguna** mas el registro de re-ejecucion.

**Re-ejecucion no modificada (log completo en `k4_reproduction_log.txt`):** entorno identico al declarado en `DEPENDENCIAS.txt` de P3 (python 3.12.14, numpy 2.1.3, misma plataforma); `python3 k4_edge_costes.py` → exit 0; el CSV regenerado es **byte-exacto** contra el sellado (sha256 `c00fce39abef9600efeb4a38742e2a9fd5998a559a6203f45c2d173fdf5687a2`, `cmp` sin diferencias); el paquete P3 fue restaurado y re-verificado integro tras la prueba. **Estatus K4 desde GLM:** `GENERATOR_EXISTS_GLM_SELF_REPRO_BYTE_EXACT` — reproducibilidad propia, no sustituto de la reproduction independiente de AUD. Las cifras de K4 (hurdle 2.75¢/share a 50¢; 117% del precio en longshots; latencia 1 s vs señal de vida 8 s) siguen siendo **supuestos de escenario elegidos**, no costes universales de BTC5m ni EV observado (AUD #206 11:28Z).

## 6. Sintesis del cierre

- K2/K3/K5/K6: `SYNTHETIC_NUMERIC_REPRODUCED` (AUD); K6 con etiquetas corregidas aqui; K5 con caveat de seleccion de intentos.
- K4: generador aportado en este paquete; queda a disposicion de AUD para su verificacion independiente.
- K1/K7: matrices documentales; sin acceso a autoridad original siguen siendo atribucion documental, no evidencia.
- Espacio RED: cubierto por RED-01..05 (corregidos) + D1–D4; se anaden exactamente tres especificaciones nuevas no duplicativas (RED-06/07/08) alineadas con los hallazgos de decidabilidad de P4.
