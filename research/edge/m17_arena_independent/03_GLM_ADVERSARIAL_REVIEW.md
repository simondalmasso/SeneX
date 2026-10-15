# M17 ARENA INDEPENDENT — 03: REVISIÓN ADVERSARIAL DE GLM P3/P4 (INV-3)
Base documental: cuerpos y comments completos de #206/#208 + intake AUD (K2/K3/K5/K6 = SYNTHETIC_NUMERIC_REPRODUCED por AUD; K4 script no entregado). Los originales de GLM P4 NO están en el repo (PR #210 = inbox vacío con README) ⇒ todo lo no registrado en issues queda `GLM_REPORT_ONLY_UNREPRODUCED` y no lo juzgo como si lo hubiera leído.

## Qué NO repito (ya detectado por AUD — reconocido, no mérito mío)
Synthetic-vs-empírico de K2/K3/K5/K6; 2.75¢/117% como supuestos de escenario y no costos universales; P(fill)=0.44 no identificable desde book; error dimensional Q99(|ΔTWAP60|) vs p_shock; D2/D3 no son White-RC/SPA completos; D4 no es DSR real; contradicción 0.1573-vs-~19%; K5 τ=.03 celda +0.91 contra "todo estrictamente negativo".

## ERRORES MATERIALES NUEVOS

### GLM-E1 (P4, Q1) — Omisión material: la capa de LABEL es decidible y GLM la declaró indecidible por agregación
**Error**: la matriz de decidibilidad de P4 (según #208 y sus comments) trata "terminal market label source" como Gamma/relay y concluye indisponibilidad histórica global. **El registro on-chain `ConditionResolution` no aparece en ninguna parte de M17** (grep sobre #201/#205/#206/#208 completos = 0 menciones). Evidencia ejecutada hoy: labels históricos oficiales, gratuitos, inmutables, cosechables a escala (96/66min), con timestamp exacto (01/E1-E2).
**Impacto**: flip de `HISTORICAL_SETTLEMENT_REPLAY=NOT_IDENTIFIABLE` (enunciado global) a **`LABELS=IDENTIFIABLE / TWAP_INPUTS=NOT_IDENTIFIABLE`** (enunciado correcto). Cambia qué ciencia es legal HOY: toda evaluación retrospectiva que solo necesite labels oficiales + cuota-proxy deja de estar bloqueada.
**Certeza**: ALTA (evidencia primaria propia). Lo que NO afirmo: que GLM haya negado explícitamente la vía on-chain — la omitió, que para una orden de "source decidability" es el error material.

### GLM-E2 (K5) — Transferencia de efecto: el "kill" sintético es irrelevante a escala M17 por tamaño de efecto
**Error**: K5 reporta pérdida −418.33 con z=3.1 sobre N=500.000 trials sintéticos como demostración de que mejor Brier ⇒ PnL negativo. Aritmética: efecto por trial = 418.33/500000 ≈ **8.4e-4 unidades** (unidad nunca ligada a bankroll/contrato — defecto adicional); z escala con √N, luego el mismo efecto a n=500 reales (techo plausible de un cohort M17) da z≈0.098: **indistinguible de cero**. El contraejemplo lógico (Brier≠EV) es válido y lo confirmo; pero presentar ESTA simulación como evidencia de peligro económico material es un non sequitur de potencia: construyó un efecto que solo es "significativo" porque se dio 500k muestras.
**Qué debería decir**: "existe un contraejemplo lógico; su magnitud en M17 es no identificada". **Certeza**: ALTA sobre la aritmética; el número −418.33/z=3.1/N=500k está registrado por AUD en #208.

### GLM-E3 (K6, fase C) — Dirección del sesgo: la FPR familiar sintética asume independencia y por eso SOBRE-estima el riesgo real
**Error**: C reporta FPR por familia 0.5373 ≈ teórico 1−0.95^15 = 0.5367 — es decir, sus 15 configuraciones son (por construcción) estadísticamente independientes. Las familias de variantes reales de SENEX (umbrales vecinos, mismas features, mismas ventanas) están **fuertemente correlacionadas positivamente**, y con correlación ρ→1 la FWER→0.05, no 0.54. Usar el número del caso independiente como "riesgo de falso descubrimiento de SENEX" es tomar el peor caso como estimador puntual sin declararlo.
**Matiz importante**: la dirección del sesgo REFUERZA la necesidad de corrección por multiplicidad (el riesgo real está entre 0.05 y 0.54), pero el 46.7%/53.7% como constante transferible a M17 es inválido — igual que AUD objetó la precisión Monte Carlo (34-59%), yo objeto la ESTRUCTURA de dependencia. Dos razones independientes para no transferir el número.
**Certeza**: ALTA (el match 0.5373≈1−0.95^15 está en el propio CSV reproducido por AUD y delata la independencia).

### GLM-E4 (K3) — Conflación de brazos: P(fill)=0.44 aplicado uniformemente no es cota conservadora para el brazo taker
**Error estructural** (más allá de "no identificable desde book", ya dicho por AUD): una probabilidad de fill de cola/maker NO aplica al brazo de ejecución marketable/taker, donde el fill hasta la profundidad visible dentro de la ventana de frescura de la cuota es cuasi-determinista y el costo verdadero es spread+fee. Aplicar 0.44 uniforme a toda estrategia: (a) sesga EV hacia abajo en el brazo taker ⇒ NO es "conservador", es incorrecto de signo ambiguo (mata estrategias viables taker, y a la vez puede favorecer maker al ignorar adverse selection del fill condicional); (b) una cota solo es cota del brazo que dice acotar. K3 debería producir DOS modelos de costo por brazo, no un escalar.
**Certeza**: MEDIA-ALTA — conceptual sobre el diseño registrado de K3 (lognormal depth/queue + sweep, descrito por AUD); si el K3 completo (no entregado) ya separa brazos, este punto cae — está formulado como falsable contra el artefacto cuando se publique en el inbox #210.

### GLM-E5 (proceso, P4) — "Completado en sandbox externo" sin publicación = resultado no existente a efectos de M17
A la fecha de este informe, PR #210 contiene SOLO el README del inbox; los 39 archivos del manifiesto P3 tienen 31 no entregados a AUD; P4 "completo" vive en `/home/z/my-project/download/` de un entorno ajeno. Por las propias reglas del programa (local-only ≠ evidencia durable; lo firmó AUD en ORDER087 y la escalera M17), **el estado científico real de P4 es `UNDELIVERED`**, y cualquier dictamen que lo cite como terminado comete el mismo error que GLM: confundir "lo escribí" con "existe auditablemente".

## Qué CONFIRMO de GLM (crédito explícito)
1. La no-replayabilidad histórica de los INPUTS TWAP60 (mi E3 del doc 01 la confirma por la vía más fuerte: ausencia estructural en la tx de resolución, no solo ausencia de API).
2. El contraejemplo lógico Brier≠EV (K5 como teorema de existencia, no como medida).
3. La agenda K2 de causalidad T0 y relojes (sana, debe quedar en los RED de ARQ).
4. La insistencia en que relay ≠ atestación firmada — correcta y confirmada por mi decode (ni siquiera la resolución on-chain contiene el reporte).
