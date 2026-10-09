# M17 ARENA INDEPENDENT — 04: TRES FALSADORES DECISIVOS (INV-4)
Criterios: no cubiertos por ARQ #205 (D1-D4 = custodia de bytes/esquemas/fees/fill-gate) ni por K1-K7 (fixtures sintéticos); costo $0; datos accesibles HOY (históricos públicos, no prospectivos); alto poder de falsación; mejor-Brier NO cuenta como rentabilidad; comparadores A_NO_TRADE / B_MARKET_PRIOR / C_NATIVE_300S / intercept-only se mantienen separados.

Las tres patas de datos ya están verificadas (doc 02): labels on-chain + CLOB prices-history de mercados cerrados + fees/rule bytes por mercado.

---

## FALS-1 — Dominancia del prior con costos: "si nadie-sin-oráculo puede, SENEX tampoco"
**Hipótesis a falsar**: "existe edge neto ejecutable en BTC5m para SENEX".
**Diseño (offline, retrospectivo)**: corpus N≥300 mercados 5m resueltos (labels on-chain; ventanas no solapadas ⇒ independencia limpia, sin bootstrap heroico). Para cada mercado: última cuota pre-cierre disponible en prices-history a T0−δ (δ≥60s, declarado ex ante) + fee del mercado. Evaluar las CUATRO referencias por separado: A_NO_TRADE (EV=0 por definición, el listón), B_MARKET_PRIOR (comprar el lado que el precio ya favorece), intercept-only (siempre UP — relevante: tie⇒UP + drift), C_NATIVE_300S si hay T0 nativo custodiado (si no: `NOT_EVALUABLE`, jamás retro-construido).
**Lógica de kill**: las cuotas-proxy sin book son **cota OPTIMISTA** (fill al precio visto, sin slippage ni profundidad). Si bajo cota optimista ni B ni intercept-only superan A_NO_TRADE neto de fees (IC 95% por mercado-bloque), entonces el excedente de información necesario para que CUALQUIER modelo supere costos queda medido en ≥ el gap observado — y SENEX, que sobre el prior aporta Brier −0.0004 e **−0.94pp** direccional (ORDER099, holdout real), no puede cerrarlo. Kill por dominancia: no requiere evaluar a SENEX en sí, inmune a "no teníamos T0 de SENEX".
**Qué lo refutaría a él**: B o intercept-only con EV neto >0 estable bajo cota optimista ⇒ el mercado 5m es ineficiente a nivel precio público ⇒ M17 sigue vivo y FALS-1 lo documenta con tamaño de efecto.
**STOP preregistrable**: EV_neto(B) y EV_neto(UP-always) con IC95 superior < 0 ⇒ `BTC5M_ECONOMIC=KILLED_BY_DOMINANCE`.

## FALS-2 — Cota de divergencia label-oficial vs proxy-exchange: techo duro de transferencia
**Hipótesis a falsar**: "un modelo entrenado/validado con features y labels de exchange transfiere su WR al settlement oficial".
**Diseño**: mismos N mercados; label oficial on-chain vs sign(close−open) del mismo bucket 5m en data.binance.vision (y OKX como tie-break de venue). Medir **δ̂ = tasa de desacuerdo** con IC binomial exacto. Estratificar por |move| (la divergencia vive cerca de cero: TWAP60 ≥ ref con tie⇒UP vs close>open son reglas distintas por construcción).
**Lógica de kill**: cualquier WR_proxy medido en backtest exchange se degrada a lo sumo a WR_oficial ≥ WR_proxy − δ̂ (y típicamente ≈ WR_proxy·(1−δ̂) + (1−WR_proxy)·δ̂ bajo independencia del error). Con margen bruto requerido ≈ fee+spread (≥1.75-2.75¢ ≈ 3.5-5.5pp de WR en p≈0.5), **si δ̂ ≥ ese margen, TODA la familia de evidencia exchange-proxy (incluidos los features nativos 300s de SENEX) queda estructuralmente insuficiente para probar edge neto oficial** — cierre de una clase entera de experimentos, no de uno.
**Novedad vs K1**: K1 pidió distinguir fuentes; FALS-2 **mide el número que decide** si la distinción es económicamente letal o ignorable. Nadie en M17 lo ha medido.
**STOP**: δ̂_IC95_inferior ≥ margen_bruto_mínimo ⇒ `EXCHANGE_PROXY_EVIDENCE=INADMISSIBLE_FOR_NET_EDGE`.

## FALS-3 — Masa de frontera inadjudicable: la zona donde el edge es incomprobable por construcción
**Hipótesis a falsar**: "el edge declarado es verificable en todo el soporte de resultados".
**Diseño**: con el corpus de FALS-2, medir la masa de mercados en zona de frontera: |move_exchange| < q (q = percentil a declarar ex ante, p.ej. el |Δ| que TWAP60-vs-spot puede invertir, estimable con la varianza intra-bucket de 1m klines) y/o última cuota en [0.5−κ, 0.5+κ]. Del doc 01: los inputs TWAP históricos son irrecuperables ⇒ **en la zona de frontera el label oficial es incontrastable contra cualquier reconstrucción independiente** (ni para auditar ni para disputar).
**Lógica de kill**: si una estrategia (p.ej. momentum fin-de-ventana, la familia natural a 5m) concentra sus señales en frontera, su PnL retrospectivo depende de labels inadjudicables emitidos por una clave centralizada (01/E2) — el claim se vuelve **unfalsifiable**, que en este programa equivale a inadmisible. Output: masa de frontera m̂ + regla dura de exclusión (todo cohort futuro debe excluir frontera o declarar el label como acto de fe cuantificado).
**STOP**: si m̂ ≥ 20% y el EV condicional fuera de frontera cae bajo 0 (enlace con FALS-1), la familia momentum-5m muere entera.

---

### Por qué estos tres y no otros
Juntos cierran el triángulo que M17 tiene abierto: **(1) ¿el mercado deja dinero sobre la mesa a precio público? (2) ¿la evidencia proxy transfiere al label que paga? (3) ¿el label es siquiera auditable donde el edge viviría?** Un NO en cualquiera mata la línea BTC5m sin necesidad de tocar ORDER100, sin cohort prospectivo, sin crédito de vendor y sin un solo byte de SENEX. Y los tres producen números con unidades e IC, no adjetivos.