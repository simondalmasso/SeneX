# SENEX Fase 1 — Paquete de validacion de calibracion (REGENERATED v2.1)

**PROVENANCE**: REGENERATED 2026-10-19 por GLM, reconstruido desde el protocolo
de la conversacion tras el incidente de sandbox que destruyo el paquete v1
(commit `0ec06f7`, harness sha256 `7cd9a423...`). **Este NO es el paquete
original**: codigo nuevo, hashes nuevos, mismo protocolo congelado. Los bytes
v1 son recuperables solo de las descargas locales del owner (si existen).
El fixture es evidencia sintetica TEST-ONLY: jamas historica, jamas autoritativa.

## Contenido

| Archivo | Rol |
|---|---|
| `fase1_harness.py` | Harness: controles de integridad del seal + identidad obligatoria commit/tree/build + pipeline y compuertas G1/G2/G3 |
| `fase1_seal_selftest.py` | TEST1 seal valido generado por el runtime (7/7 PASS) / TEST2 tamper de 1 fila (doble rechazo rows_hash+seal_hash) |
| `fase1_fixture_gen.py` | Generador determinista (seed 20261018) de 300 filas que califican el contrato completo de proof |
| `fase1_fixture_test.jsonl` | Fixture sintetico TEST-ONLY (salida del generador; regenerable byte-exacto) |
| `log_selftest.txt` | Corrida real del selftest (stdout + exit code) |
| `log_synthetic_run.txt` | Corrida real del harness sobre el fixture (stdout + exit code; compuertas FAIL — no rubber stamp) |
| `README_PACKAGE.md` | Este documento |
| `MANIFEST.sha256` | SHA-256 de los 7 archivos de payload |

## Requisitos

Python 3.12+, numpy, scikit-learn (PlattCalibrator del runtime usa
LogisticRegression). Un checkout del repo SeneX (cualquier arbol que contenga
`senecio_polymarket/backend/` idéntico al desplegado `933f0619`; en el arbol
de trabajo verificado, `authority_seal.py`, `authoritative_score.py` y
`settlement_proof.py` son byte-identicos al runtime H011).

## Comando de verificacion (solo seal autentico; commit, tree y build digest esperados son obligatorios)

```bash
# Sustituir los tres placeholders por valores del readback independiente actual de H011.
python3 fase1_harness.py authority-BTCUSDT.json \\
    --expect-commit SOURCE_COMMIT_FROM_FRESH_RUNTIME_READBACK \\
    --expect-tree SOURCE_TREE_FROM_FRESH_RUNTIME_READBACK \\
    --expect-build-digest BUILD_DIGEST_FROM_FRESH_RUNTIME_READBACK \\
    --live-parity 861,426,435,504
```

La tupla de paridad = (n, wins, losses, valid_confidence) de la cohorte 1h
independiente observada en el freeze 2026-10-18 (861/426/435/504, Wilson
0.461475). El n vivo crece con el tiempo: la paridad usa tolerancia 5% y
reporta cada componente por separado.

Los tres valores de identidad tienen que proceder de un readback fresco e
independiente del artefacto desplegado. No reutilizar hashes históricos como
si probaran el runtime actual; si la provenance del servicio no responde, el
veredicto queda bloqueado.

## Como obtener el seal autentico (accion UNICA del owner)

El archivo vive en el volumen persistente de Northflank (H011):
`/app/polymarket/results/authority_seals/authority-BTCUSDT.json`.
Copialo SIN EDITAR (Northflank: job shell / volume browser) y pegalo en la
conversacion o entregalo a GPT por canal privado. **NUNCA lo subas a GitHub**
(contains complete authority rows). Ninguna clave ni token va en el chat.

## Que verifica el harness sobre el seal (integridad + identidad exacta, pre-declarados)

1. `contract == senex-authority-durable-seal-v1`
2. orden canonico de filas por `(ts, id)` (primitivo `_row_key` del runtime)
3. `row_count == len(rows)`
4. `rows_hash == _sha(rows)` (sha256 canonico del runtime)
5. `cursor == _cursor_for_rows(rows)` (cursor = ultima fila)
6. `seal_hash == _sha(payload sin seal_hash)`
7. `source_commit == --expect-commit` (SHA esperado obligatorio)
8. `source_tree == --expect-tree` (SHA de árbol esperado obligatorio)
9. `build_digest == --expect-build-digest` (digest esperado obligatorio)

Despues: filas convertidas con `_authority_row_from_projection` (la misma
funcion que `fetch_authority_history` usa antes de sellar), filtradas por
`filter_proof_qualified`, cohorte `independent_1h_cohort`, Wilson
`_wilson_lower`, split cronologico 67/33, Platt recalibracion train-only
(maquinaria order099), y las compuertas sobre el holdout intacto.

## Compuertas pre-declaradas (holdout, sin reutilizar para elegir modelos)

- **G1**: Brier(recal) < Brier(base-rate) **y** <= 0.25 (techo max_brier del runtime)
- **G2**: ECE-10bins(recal) <= 0.05
- **G3**: discriminacion por buckets de confianza (tercil superior vs inferior):
  brecha >= 5pts Y intervalos Wilson 95% sin cruce

## 6 chequeos PENDING (requieren el seal autentico — hasta entonces BLOCKED)

1. Integridad crypto del seal (7 checks) con el archivo real
2. Match de `source_commit`/`source_tree`/`build_digest` contra el runtime vivo
3. Reproduccion de la cohorte 1h (referencia freeze: 861/426/435/504)
4. Reproduccion de Wilson lower 95 = 0.461475
5. Resultado de compuertas G1/G2/G3 sobre el holdout real
6. Reporte de discrepancias (si las hay) contra la tupla de paridad

## Verificacion tecnica ejecutada (v2, sin seal real)

- Selftest: 2/2 PASS (seal valido generado por el runtime: 7/7 checks +
  pipeline 60/60 filas calificadas; tamper de 1 fila: doble rechazo).
- Corrida sintetica: pipeline completo 300/300 filas calificadas, compuertas
  FAIL (G1 0.2597 vs 0.2499; G2 0.1063; G3 +3.3pts) — las compuertas no son
  rubber stamp; una corrida sintetica PASS seria solo reproducibilidad tecnica.

## Publicacion (para GPT, con permisos de push)

Colocar los 8 archivos en `audit/fase1_harness_v2_20261019/` sobre un checkout
de `933f0619`, commit docs-only en rama aislada, PR DRAFT sin merge (recordar:
cualquier push dispara el espejo a GitLab). El commit v2 sera un SHA NUEVO —
no es ni pretende ser `0ec06f7`. Verificacion de integridad: `sha256sum -c
MANIFEST.sha256` sobre los 7 archivos de payload.

## Estados (sin cambio)

`AUTHORITY_SCORE=REJECTED` · `EDGE=UNPROVEN` · `PROBABILITY_SEMANTICS=UNVALIDATED`
`PAPER_ONLY=true` · `ZERO_SPEND=HARD` · `LIVE=false` · `REAL_ORDERS=0` · `CAPITAL=0`


## v2.1 regression tests

Run: `python -m unittest -v test_fase1_harness_v21.py`. Tests cover parity
mismatch (exit 3 and no verdict), parity match (continues to the predeclared
sample gate), missing identity expectations, missing or mismatched tree/build
identity, and rejection of unsealed CSV/JSONL. SKIP remains visible for
diagnostics but cannot satisfy the acceptance contract. Tests are synthetic
control-flow tests, not evidence from the production seal.


## v2.1 acceptance behavior

- Exit 3: live/export parity differs by more than 5% in any declared component; the harness stops before any Fase 1 verdict.
- Exit 2: evidence blocked, required identity expectation missing/malformed/mismatched, unsealed CSV/JSONL supplied, or sample/holdout below predeclared minimums.
- Exit 1: the Fase 1 statistical gates fail.
- Exit 0: all Fase 1 statistical gates pass; this is not by itself authorization for live trading.
- `--expect-commit`, `--expect-tree`, and `--expect-build-digest` are all mandatory. Values must be correctly formatted and independently verified against the current runtime; any missing or discrepant identity fails closed before scoring. SKIP is never an acceptance pass.
- CSV/JSONL exports cannot produce an accepted verdict because they do not authenticate producer identity through the durable seal contract.
- The v2.1 unit tests use synthetic in-memory data and do not authenticate the production seal or establish statistical edge.
