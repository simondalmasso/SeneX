#!/usr/bin/env python3
"""SENEX Fase 1 — Selftest del durable seal (REGENERATED v2).

PROVENANCE: REGENERATED 2026-10-19. Not the lost v1 selftest (sha256
9b134cc1...). Same two-test protocol, new bytes.

TEST 1 — generate a VALID durable seal with the RUNTIME's own
         save_authority_state (identity mirrored from the live runtime
         readiness probe: commit 933f0619, tree 99ce7a7e, build 9355a157),
         then verify the harness's 7 crypto checks all PASS and the
         scoring pipeline runs on the seal's rows.

TEST 2 — tamper exactly one row of the saved seal (flip one outcome) and
         re-verify: the harness must reject on BOTH rows_hash AND seal_hash
         (double rejection). A single-check rejection would mean the tamper
         was detected for the wrong reason or partially.

The seal is written to a temp dir via the runtime's own env override
(SENEX_AUTHORITY_SEAL_DIR) — never to the real /app volume. Exit 0 only if
both tests behave exactly as pre-declared.
"""
from __future__ import annotations

import json
import os
import random
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import fase1_fixture_gen as fg  # noqa: E402
from fase1_harness import _import_runtime, _resolve_backend, verify_seal  # noqa: E402


def main() -> int:
    backend = _resolve_backend(None)
    print(f"[selftest REGENERATED v2] backend: {backend}")
    rt = _import_runtime(backend)
    from backend.authority_seal import save_authority_state  # noqa: E402
    from backend.supabase_client import AUTHORITY_MUTATION_CONTRACT  # noqa: E402

    tmp = tempfile.mkdtemp(prefix="fase1_seal_selftest_")
    os.environ["SENEX_AUTHORITY_SEAL_DIR"] = tmp

    rng = random.Random(4242)
    rows = [fg.build_row(i, rng) for i in range(60)]
    rows = sorted(rows, key=rt["_row_key"])
    cursor = rt["_cursor_for_rows"](rows)
    identity = {
        "source_commit": fg.LIVE_SOURCE_COMMIT,
        "source_tree": fg.LIVE_SOURCE_TREE,
        "build_digest": fg.LIVE_BUILD_DIGEST,
    }
    payload = save_authority_state(
        "BTCUSDT", rows, cursor,
        identity=identity,
        writer_contract=AUTHORITY_MUTATION_CONTRACT,
    )
    seal_path = Path(tmp) / "authority-BTCUSDT.json"
    print(f"[setup] runtime save_authority_state -> {seal_path} "
          f"({payload['row_count']} rows, scope={payload['scope']})")

    failures = 0

    # ---- TEST 1: valid seal -> 7/7 PASS + pipeline ----
    print("\n=== TEST 1: seal valido generado por el runtime ===")
    with open(seal_path, encoding="utf-8") as fh:
        good = json.load(fh)
    all_ok, checks = verify_seal(good, rt, fg.LIVE_SOURCE_COMMIT)
    for name, ok, detail in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
    qualified = rt["filter_proof_qualified"](
        [rt["_authority_row_from_projection"](r) for r in good["rows"]])
    cohort = rt["independent_1h_cohort"](qualified)
    n = len(cohort)
    wins = sum(1 for r in cohort if r.get("outcome") == "WIN")
    print(f"  pipeline: qualified={len(qualified)}/{len(good['rows'])} cohort_1h={n} "
          f"wins={wins} wilson={rt['_wilson_lower'](wins, n):.6f}")
    t1 = all_ok and len(qualified) == len(good["rows"]) and n > 0
    print(f"TEST 1: {'PASS' if t1 else 'FAIL'}")
    failures += 0 if t1 else 1

    # ---- TEST 2: tampered seal -> double rejection ----
    print("\n=== TEST 2: tamper de UNA fila (outcome WIN<->LOSS) ===")
    bad = json.loads(json.dumps(good))
    flipped = 0
    for row in bad["rows"]:
        if row.get("outcome") == "WIN":
            row["outcome"] = "LOSS"
            flipped += 1
            break
    with open(seal_path, "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    all_ok2, checks2 = verify_seal(bad, rt, fg.LIVE_SOURCE_COMMIT)
    results = {name: ok for name, ok, _ in checks2}
    c4 = results.get("C4 rows_hash")
    c6 = results.get("C6 seal_hash")
    for name, ok, detail in checks2:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}: {detail}")
    print(f"  filas alteradas: {flipped}")
    t2 = (not all_ok2) and (c4 is False) and (c6 is False)
    print(f"TEST 2: {'PASS' if t2 else 'FAIL'} "
          f"(rechazo doble esperado: rows_hash={not c4}, seal_hash={not c6})")
    failures += 0 if t2 else 1

    print(f"\n[selftest] resultado: {2 - failures}/2 PASS — "
          f"{'SELLADO Y DETECCION DE TAMPER VERIFICADOS' if failures == 0 else 'FALLO'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
