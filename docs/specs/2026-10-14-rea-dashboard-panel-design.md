# Diseño técnico — Panel de evidencia histórica REA en el dashboard SENEX

**Fecha:** 2026-10-14
**Estado:** diseño aprobado en alcance general; pendiente revisión de este documento antes de implementar
**Rama de trabajo:** feat/rea-engine-lab-20261014
**Base:** 583e11588cea8594db8625a01dc0305ec826f008
**Objetivo:** mostrar dentro del dashboard actual la prueba histórica y el estado de verificación del laboratorio REA, sin presentarla como evidencia de rentabilidad futura ni como operación en vivo.

## 1. Decisión y límites

Se integrará una sección de investigación estática y de solo lectura en el frontend existente. Se conservarán el encabezado, las cuatro columnas actuales, los paneles de mercado, el estado del oráculo y los paneles PAPER ya existentes. La nueva sección aparecerá como una quinta zona de ancho completo al final de la cuadrícula principal, accesible mediante el scroll normal de main. Así se evita desplazar la observabilidad de runtime ni confundir los datos históricos con los datos del mercado actual.

No se añadirán endpoints, consultas de base de datos, conexiones de exchange, WebSockets, credenciales, acciones de ejecución ni llamadas externas nuevas. El panel consumirá una instantánea estática local con procedencia y hashes visibles. Los archivos del backend y los cinco interfaces/runtime protegidos quedan fuera de alcance.

**Restricciones de ejecución que permanecen inmutables:**

- ZERO_SPEND=HARD, RESEARCH_ONLY=true, PAPER_ONLY=true.
- LIVE=false, REAL_ORDERS=0, CAPITAL=0, NO_KEYS=true.
- Sin acceso privado a cuentas de exchange, órdenes testnet, spot, margen, futuros ni apalancamiento.
- Sin commit, push, PR, merge, despliegue, CI-trigger ni modificación de los workflows.
- EDGE=UNPROVEN se conserva hasta que exista evidencia independiente, ejecutable y fuera de muestra, neta de costes, con robustez suficiente.
- No se modifican backend/runtime ni se sustituye ningún componente de ejecución existente.

## 2. Ubicación y arquitectura frontend

### Archivos previstos

1. senecio_polymarket/frontend/index.html: añadir el contenedor semántico #rea-research-panel dentro de main y cargar los dos módulos de investigación estática.
2. senecio_polymarket/frontend/styles.css: estilos del panel y adaptación de la cuadrícula a escritorio, tablet y móvil.
3. senecio_polymarket/frontend/rea_research_snapshot.js: instantánea de datos estáticos exportada desde los informes locales; incluye identificadores de fuente, hashes, estado de seguridad y resultados seleccionados.
4. senecio_polymarket/frontend/rea_research_panel.js: renderizado del panel, tablas y avisos. No realiza fetch, no registra temporizadores y no escribe estado de runtime.
5. research/rea_engines/export_dashboard_snapshot.py: generador determinista de la instantánea a partir del informe JSON de resultados, metadatos de dataset y manifiestos locales.
6. tests/test_rea_dashboard_snapshot.py: validación de correspondencia entre el snapshot y los informes de origen, de las señales de seguridad y de las advertencias obligatorias.
7. Este documento es la especificación; no se considera implementación del panel.

Los módulos nuevos se cargarán mediante las rutas estáticas ya usadas por el frontend. El generador producirá únicamente datos agregados seleccionados y metadatos de auditoría, no copiará el informe de 11 MB, curvas completas, fills individuales ni pseudocódigo propietario a los assets del dashboard. La instantánea se regenerará desde los artefactos locales; no se mantendrán cifras editadas a mano en más de un lugar.

### Contrato de datos

El objeto de snapshot tendrá, como mínimo:

- schema_version, generated_at_utc, report_path, report_sha256, source_sha256.
- status: historical-paper, edge: UNPROVEN, live_orders_submitted: 0, network_calls: 0 (esta última cifra se refiere exclusivamente a la ejecución histórica reproducida), zero_spend: true.
- Alcance: símbolo, mercado, intervalo, cantidad de filas fuente, filas evaluadas, límites temporales, huecos de intervalo y política de fills.
- Resultados agregados por estrategia/benchmark: retorno porcentual, PnL neto en USDT, drawdown máximo, fills, porcentaje de tiempo en mercado, comisiones y turnover.
- Sensibilidad a costes por factor 1x, 2x, 3x.
- Subperiodos anuales solo cuando la fuente sea coherente y el significado de reset quede expuesto.
- Estado de tests, auditoría de inventario, verificación de hashes y limitaciones.
- Aviso de licencia visible, no oculto en un tooltip ni solo en documentación.

Si un campo no se puede respaldar con una fuente local concreta, se mostrará UNKNOWN/NOT VERIFIED; no se inferirá ni se sustituirá por cero.

## 3. Diseño de la interfaz

El panel seguirá la paleta y los componentes existentes (--bg-panel, --border, --mono, .panel, .panel-head, .risk-grid). Se usará una cabecera con el título **REA · Historical research evidence**, una etiqueta de alcance LOCAL STATIC SNAPSHOT y una advertencia persistente **HISTORICAL PAPER · EDGE UNPROVEN · LIVE=NO**.

El panel estará dividido en bloques con subtítulos claros y tablas compactas. Los bloques extensos podrán usar details/summary nativos para que el usuario pueda expandir evidencia sin hacer interminable la vista inicial. En móvil, las tablas tendrán scroll horizontal controlado y las tarjetas pasarán a una columna; ningún estado de seguridad desaparecerá en resoluciones estrechas.

### 3.1 Resumen y procedencia

Mostrar:

- Mercado: Binance Spot BTCUSDT, velas 4h.
- Dataset fuente: 14.789 filas, desde 2020-01-01T00:00:00Z hasta 2026-09-30T20:00:00Z.
- Intervalo evaluado fuera de muestra (OOS): 2022-01-01T00:00:00Z a 2026-09-30T20:00:00Z, 10.404 filas.
- SHA-256 del CSV: 7256f0ed5848182316c4ab9046b8ab23980b47d04a91a129ad77738882068146.
- SHA-256 del informe reproducido: 08cdad21c7dbc0d2c1128aca21d955d274d85f451c0715b50edf6b5113b2d89d.
- Política de fills: ohlcv-next-open-resting-limit-v1.
- Perfil de costes configurado: comisión 10 bps, slippage 5 bps y spread 5 bps; la tabla de resultados reflejará los costes contabilizados en cada modelo, no asumirá que cada componente se aplica idénticamente a cada fill.
- Estado de seguridad del reporte: historical-paper, live_orders_submitted=0, network_calls=0 en esa reproducción, zero_spend=true, EDGE=UNPROVEN.

El panel debe decir que el backtest usa OHLCV y una política aproximada de ejecución; no conoce prioridad real en cola, profundidad ni probabilidad real de fills parciales.

### 3.2 Resultados agregados

Mostrar las estrategias limpias por separado de los benchmarks, con columnas para retorno, PnL neto, drawdown máximo, número de fills, tiempo en mercado, comisiones y turnover. Los valores base confirmados en el informe son:

| Modelo | Retorno | PnL neto (USDT) | Drawdown máx. | Fills | Tiempo en mercado |
|---|---:|---:|---:|---:|---:|
| Grid clean-room | +0,1625% | +16,2525 | 0,1596% | 26 | 2,1530% |
| DCA clean-room | +1,8645% | +186,4482 | 1,5979% | 179 | 99,3560% |
| Combo clean-room | +0,3205% | +32,0519 | 0,2399% | 22 | 1,1342% |
| Buy & hold, asignación de 100 USDT | +0,7846% | +78,4575 | 1,4082% | 1 | 99,9904% |
| Buy & hold, cantidad fija de 0,02 BTC | +7,3457% | +734,5693 | 11,5863% | 1 | 99,9904% |

Las dos variantes buy-and-hold estarán rotuladas como benchmarks, no como estrategias directamente comparables: una asigna solo 100 USDT de una base de 10.000 USDT; la otra usa una cantidad fija de 0,02 BTC y tiene una exposición diferente. No se creará un ranking de “ganador” que ignore esas diferencias.

### 3.3 Sensibilidad a costes

Tabla por estrategia/benchmark y factores 1x, 2x, 3x, con retorno, drawdown y fills. Resultados de retorno que el snapshot debe preservar:

- Grid: +0,1625%, +0,1624%, +0,1373%.
- DCA: +1,8645%, +1,6870%, +1,4193%.
- Combo: +0,3205%, +0,3084%, +0,2968%.
- Buy-and-hold con asignación de 100 USDT: +0,7846%, +0,7828%, +0,7811%.
- Buy-and-hold de 0,02 BTC: +7,3457%, +7,3293%, +7,3129%.

La sensibilidad no se describirá como robustez estadística ni como prueba de edge; solo como stress mecánico de los costes configurados.

### 3.4 Subperiodos anuales y anomalía del reporte

Se mostrará la serie anual de DCA con retorno, drawdown, fills y exposición. Cada año reinicia el capital de referencia; **no** representa una curva compuesta ni una descomposición exacta del resultado agregado:

| Año | Retorno DCA | Drawdown máx. | Fills |
|---|---:|---:|---:|
| 2022 | -1,1882% | 1,5979% | 28 |
| 2023 | +1,3161% | 0,3306% | 94 |
| 2024 | +1,4568% | 0,4691% | 114 |
| 2025 | +0,0117% | 0,6975% | 45 |
| 2026 YTD | -0,0394% | 0,8587% | 10 |

El snapshot debe detectar y exponer una anomalía: las métricas del subperiodo 2022 para Grid y Combo coinciden exactamente con sus métricas agregadas base, mientras los subperiodos posteriores también reportan actividad. Hasta auditar el generador de subperiodos, esos datos no se presentarán como una serie anual validada. El panel mostrará una advertencia **ANNUAL GRID/COMBO SLICES: VALIDATION REQUIRED** y omitirá esos valores de cualquier gráfico comparativo de rendimiento anual. No se “arreglará” la fuente mediante inferencias.

### 3.5 Evidencia de tests y auditoría

El panel separará los resultados verificados de los bloqueos:

- Tests de REA: 47/47 aprobados (28 de base + 19 de resiliencia).
- compileall: aprobado.
- Auditoría AST/imports: sin imports prohibidos de red, exchange o runtime en el paquete REA inspeccionado.
- git diff --check: aprobado en la auditoría existente; se volverá a ejecutar tras implementar.
- Inventario de fuentes: 67/67 rutas resueltas, sin duplicados ni diferencias de tamaño/hash detectadas.
- ZIP de auditoría estática: 48/48 entradas cotejadas contra su manifiesto de hashes; esto verifica integridad, no equivalencia semántica del ejecutable.
- Cinco interfaces runtime protegidas: sin modificaciones.
- Suite global SENEX: **NO VERIFICADA**. La colección del test focalizado tests/test_health_readiness.py queda bloqueada en este entorno por la dependencia ausente sse_starlette; no instalar dependencias ni afirmar que toda la suite pasó.

Las categorías de resiliencia se mostrarán con su alcance: ambigüedad intrabar, fills parciales, idempotencia, checkpoints/reanudación y reconciliación. El texto debe distinguir “tests unitarios REA verdes” de “suite completa SENEX no verificada”.

### 3.6 Dataset ETH de seguimiento

Se puede mostrar únicamente como estado de ingestión/auditoría, no como rendimiento: ETHUSDT Spot 4h, 81 archivos mensuales verificados, 14.789 filas y un hueco de 8 horas en febrero de 2020 que no se debe rellenar artificialmente. El backtest REA de ETH aún no está ejecutado y no se inventarán resultados.

### 3.7 Licencia y uso permitido

El panel y la instantánea son de **investigación local**. Los términos identificados para Binance Vision están bajo CC BY-NC-SA 4.0 y restringen el uso comercial/integración en productos comerciales o trading propietario sin autorización escrita aplicable. El aviso deberá ser visible y explícito: **NO DEPLOY / NO COMMERCIAL INTEGRATION UNTIL RIGHTS ARE CLEARED**. El snapshot no se promocionará como listo para producto ni se desplegará. Los datos/resultados de ETH no se mostrarán como resultado de estrategia. Si los derechos no se aclaran, el panel debe retirarse o sustituir sus métricas por una fuente con licencia adecuada antes de cualquier distribución comercial.

## 4. Seguridad, accesibilidad y robustez

- Renderizar valores mediante textContent y crear nodos DOM; no interpolar datos de reportes sin escapar dentro de innerHTML.
- No añadir botones que impliquen ejecutar, reanudar, ordenar, actualizar un modelo o llamar a un exchange.
- No añadir polling ni setInterval al módulo REA.
- Etiquetar los datos como diagnósticos de investigación estática y mantener separados los estados API/runtime.
- Usar aria-label, encabezados de tabla reales y texto explícito para UNKNOWN, “no verificado” y los avisos de licencia.
- Si el snapshot no existe, falla la validación o su esquema no coincide, mostrar REA SNAPSHOT UNAVAILABLE / UNKNOWN, sin sustituirlo por cifras hard-coded.
- El panel no alterará el estado de #safety-strip, los dominios de salud ni el criterio de autoridad del oráculo.
- Mantener el diseño adaptable: escritorio en fila completa, tablet en ancho completo tras las columnas y móvil con tarjetas apiladas y tablas desplazables.

## 5. Plan de verificación y criterios de aceptación

1. Generar snapshot desde los informes locales y comprobar que el hash del dataset, hash del informe, métricas, cost-stress y estado EDGE=UNPROVEN coinciden con las fuentes.
2. Ejecutar tests REA y el nuevo test de snapshot; no instalar paquetes.
3. Ejecutar python -m compileall para el generador y módulos REA.
4. Comprobar sintaxis de JS con el runtime Node disponible; si no está disponible, marcarlo como bloqueado en lugar de declarar éxito.
5. Comprobar IDs únicos en HTML, rutas de assets estáticos y que el módulo de panel no llama fetch, WebSocket, timers ni endpoints.
6. Ejecutar los tests frontend existentes que puedan ejecutarse sin modificar dependencias.
7. Ejecutar git diff --check, comprobar el diff y verificar que solo cambien los archivos frontend nuevos, el generador, los tests y la documentación autorizada.
8. Volver a verificar hashes/estado de los cinco archivos runtime protegidos y confirmar cero commits/pushes/PRs/merges/despliegues.
9. Inspeccionar el resultado en escritorio y móvil si hay navegador local autorizado; de lo contrario, declarar explícitamente que la comprobación visual interactiva quedó pendiente.

El trabajo se considerará completado solo si los criterios ejecutables anteriores se informan con salida observada. No se declarará verde la suite completa SENEX si continúa bloqueada por sse_starlette.

## 6. Fuera de alcance

- Reescritura de backend, ejecución, adaptadores de mercado, oráculo, base de datos o seguridad runtime.
- Uso de credenciales, cuentas privadas, API keys, testnet, live orders, capital o servicios pagos.
- Cambios a ORDER100_NO_TOUCH, ORDER197_NO_TOUCH, PR214_NO_TOUCH, PR215_NO_TOUCH, H011_NO_TOUCH.
- Push, commit, PR, merge, despliegue o disparo de CI.
- Reproducción semántica del ejecutable comercial, extracción adicional de pseudocódigo o copia de estrategias propietarias.
- Declaraciones de rentabilidad futura, edge demostrado o recomendación de inversión.
