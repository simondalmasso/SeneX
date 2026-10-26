#!/usr/bin/env python3
"""SENEX Fase 1 — Harness de validacion de calibracion (REGENERATED v2).

PROVENANCE: REGENERATED 2026-10-19 by GLM from the conversation protocol
after the sandbox incident destroyed the v1 artifacts (commit 0ec06f7,
harness sha256 7cd9a423...). This is NEW code implementing the SAME frozen
protocol. It is not presented as the original package and its hashes differ.
Original bytes are recoverable only from the owner's local downloads, if any.

WHAT IT DOES (frozen protocol, unchanged from v1):
  1. Loads evidence only from a durable authority seal
     (authority-BTCUSDT.json, contract senex-authority-durable-seal-v1,
     the file the owner must copy from the Northflank volume
     /app/polymarket/results/authority_seals/). CSV/JSONL exports do not
     bind rows to producer identity and cannot produce an accepted verdict.
  2. Seal mode verifies 7 crypto checks with RUNTIME primitives imported
     from the deployed backend (byte-identical at 933f0619):
     contract, canonical (ts,id) row order, row_count, rows_hash,
     cursor, seal_hash, source_commit.
  3. Rows are converted with the runtime's own _authority_row_from_projection
     and scored through the runtime pipeline: filter_proof_qualified ->
     independent_1h_cohort -> Wilson lower 95.
  4. Chronological 67/33 split on valid confidences; Platt recalibration
     fitted on TRAIN ONLY (order099 machinery: PlattCalibrator).
  5. Pre-declared gates on the untouched holdout:
       G1  Brier(recal) < Brier(base-rate)  and  Brier(recal) <= 0.25
           (0.25 = runtime max_brier ceiling)
       G2  ECE-10bins(recal) <= 0.05
       G3  confidence-bucket discrimination: top-vs-bottom tercile win rates
           differ by >= 5pts AND Wilson 95% intervals do not cross

FROZEN VERIFICATION COMMAND (real seal, from Northflank volume):
  # Replace all identity placeholders with fresh, independent H011 readback values.
  python3 fase1_harness.py authority-BTCUSDT.json \\
      --expect-commit SOURCE_COMMIT_FROM_FRESH_RUNTIME_READBACK \\
      --expect-tree SOURCE_TREE_FROM_FRESH_RUNTIME_READBACK \\
      --expect-build-digest BUILD_DIGEST_FROM_FRESH_RUNTIME_READBACK \\
      --live-parity 861,426,435,504

  (parity tuple = 1h-cohort n,wins,losses,valid_confidence observed at the
   2026-10-18 freeze; the live n grows with time, so parity uses a 5%
   tolerance and reports each component explicitly)

EXIT CODES: 0 = all gates PASS; 1 = one or more gates FAIL; 2 = BLOQUEO
(insufficient qualified data: cohort n<100 or holdout<50). A synthetic or
REGENERATED run passing these gates is TECHNICAL reproducibility only —
it is NOT scientific validation. Scientific validation requires the
authentic seal; until then AUTHORITY_SCORE=REJECTED, EDGE=UNPROVEN.

Usage:
  python3 fase1_harness.py <authority-BTCUSDT.json>
      --expect-commit <40-hex> --expect-tree <40-hex>
      --expect-build-digest sha256:<64-hex>
      [--live-parity n,w,l,vc] [--backend-dir DIR]

Backend resolution order: --backend-dir, env SENEX_BACKEND_DIR, then
./senecio_polymarket, ../senecio_polymarket, and the GLM sandbox worktree.
The path must be the PARENT of backend/ (the senecio_polymarket directory).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys

# ---------------------------------------------------------------------------
# Runtime backend resolution + imports (byte-identical primitives @933f0619)
# ---------------------------------------------------------------------------

_SANDBOX_DEFAULT = "/home/z/my-project/audit/order087/m17p4_wt/senecio_polymarket"


def _resolve_backend(explicit: str | None) -> str:
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("SENEX_BACKEND_DIR")
    if env:
        candidates.append(env)
    candidates += ["./senecio_polymarket", "../senecio_polymarket", _SANDBOX_DEFAULT]
    for cand in candidates:
        if os.path.isfile(os.path.join(cand, "backend", "authority_seal.py")):
            return os.path.abspath(cand)
    print("ERROR: no SENEX backend found (need <dir>/backend/authority_seal.py).")
    print("Pass --backend-dir pointing at the senecio_polymarket directory.")
    raise SystemExit(2)


def _import_runtime(backend: str):
    sys.path.insert(0, backend)
    from backend.authority_seal import (  # noqa: WPS433
        SEAL_CONTRACT,
        _cursor_for_rows,
        _row_key,
        _sha,
    )
    from backend.authoritative_score import (  # noqa: WPS433
        _confidence,
        _wilson_lower,
        independent_1h_cohort,
    )
    from backend.settlement_proof import filter_proof_qualified  # noqa: WPS433
    from backend.supabase_client import (  # noqa: WPS433
        _authority_row_from_projection,
    )
    from backend.research._calibration_primitives import (  # noqa: WPS433
        PlattCalibrator,
        brier_score,
        expected_calibration_error,
    )
    return {
        "SEAL_CONTRACT": SEAL_CONTRACT,
        "_cursor_for_rows": _cursor_for_rows,
        "_row_key": _row_key,
        "_sha": _sha,
        "_confidence": _confidence,
        "_wilson_lower": _wilson_lower,
        "independent_1h_cohort": independent_1h_cohort,
        "filter_proof_qualified": filter_proof_qualified,
        "_authority_row_from_projection": _authority_row_from_projection,
        "PlattCalibrator": PlattCalibrator,
        "brier_score": brier_score,
        "expected_calibration_error": expected_calibration_error,
    }


# ---------------------------------------------------------------------------
# Seal verification (7 checks) and evidence loading
# ---------------------------------------------------------------------------

def verify_seal(payload: dict, rt: dict, expect_commit: str | None,
                expect_tree: str | None = None, expect_build: str | None = None):
    """Return True only when every seal and producer-identity check passes."""
    rows = payload.get("rows")
    checks = []
    ok = payload.get("contract") == rt["SEAL_CONTRACT"]
    checks.append(("C1 contract", ok, str(payload.get("contract"))))
    ordered = sorted((dict(r) for r in rows), key=rt["_row_key"]) if isinstance(rows, list) else []
    ok = isinstance(rows, list) and ordered == rows
    checks.append(("C2 canonical (ts,id) order", ok, f"{len(rows) if isinstance(rows, list) else 0} rows"))
    ok = isinstance(rows, list) and int(payload.get("row_count", -1)) == len(rows)
    checks.append(("C3 row_count", ok, f"declared={payload.get('row_count')}"))
    ok = isinstance(rows, list) and payload.get("rows_hash") == rt["_sha"](rows)
    checks.append(("C4 rows_hash", ok, str(payload.get("rows_hash"))[:23] + "..."))
    ok = isinstance(rows, list) and payload.get("cursor") == rt["_cursor_for_rows"](rows)
    checks.append(("C5 cursor==last row", ok, json.dumps(payload.get("cursor"))))
    unsigned = dict(payload)
    unsigned.pop("seal_hash", None)
    ok = payload.get("seal_hash") == rt["_sha"](unsigned)
    checks.append(("C6 seal_hash", ok, str(payload.get("seal_hash"))[:23] + "..."))
    src = str(payload.get("source_commit") or "")
    if expect_commit:
        ok = bool(re.fullmatch(r"[0-9a-f]{40}", src)) and src == expect_commit
        checks.append(("C7a source_commit", ok,
                       src + f" (expect {expect_commit[:8]}...)"))
    else:
        checks.append(("C7a source_commit", None,
                       "SKIP unverified — pass --expect-commit"))

    tree = str(payload.get("source_tree") or "")
    if expect_tree:
        tree_ok = bool(re.fullmatch(r"[0-9a-f]{40}", tree)) and tree == expect_tree
        checks.append(("C7b source_tree", tree_ok,
                       f"{tree[:12]}... (expect {expect_tree[:12]}...)"))
    else:
        checks.append(("C7b source_tree", None,
                       "SKIP unverified — pass --expect-tree"))

    build = str(payload.get("build_digest") or "")
    if expect_build:
        build_ok = bool(re.fullmatch(r"sha256:[0-9a-f]{64}", build)) and build == expect_build
        checks.append(("C7c build_digest", build_ok,
                       f"{build[:20]}... (expect {expect_build[:20]}...)"))
    else:
        checks.append(("C7c build_digest", None,
                       "SKIP unverified — pass --expect-build-digest"))

    # SKIP is visible for diagnosis but never satisfies the acceptance contract.
    return all(c[1] is True for c in checks), checks


def load_rows(path: str, rt: dict, expect_commit: str | None,
              expect_tree: str | None = None, expect_build: str | None = None):
    """Load evidence: seal (.json w/ contract) or export (.jsonl/.csv)."""
    if path.endswith(".json"):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        if isinstance(payload, dict) and "rows" in payload:
            print(f"[0] seal detected: {path}")
            print(f"    scope={payload.get('scope')} writer_contract={payload.get('writer_contract')}")
            print(f"    identity: commit={payload.get('source_commit')}")
            print(f"              tree={payload.get('source_tree')}")
            print(f"              build_digest={payload.get('build_digest')}")
            all_ok, checks = verify_seal(payload, rt, expect_commit, expect_tree, expect_build)
            for name, ok, detail in checks:
                status = "SKIP" if ok is None else ("PASS" if ok else "FAIL")
                print(f"    {status}  {name}: {detail}")
            identity_complete = all(
                next((value for name, value, _ in checks if name == key), None) is True
                for key in ("C7a source_commit", "C7b source_tree", "C7c build_digest")
            )
            print(f"    IDENTITY_COMPLETE={'YES' if identity_complete else 'NO — not fully verified'}")
            print("    CHECKS_APPLICABLE=" + ("PASS" if all_ok else "FAIL"))
            if not all_ok:
                print("\nBLOQUEO: seal o identidad de productor incompletos/incorrectos — evidencia rechazada.")
                return None
            rows = [rt["_authority_row_from_projection"](r) for r in payload["rows"]]
            print(f"    -> {len(rows)} rows via runtime _authority_row_from_projection")
            return rows
        print("ERROR: .json without seal contract/rows — unsupported.")
        return None
    print("BLOQUEO: solo un authority seal .json con identidad completa puede producir veredicto; CSV/JSONL no atan filas a source_tree/build_digest.")
    return None

# ---------------------------------------------------------------------------
# Pipeline + gates (frozen protocol)
# ---------------------------------------------------------------------------

def _wilson_interval(wins: int, total: int, z: float = 1.959963984540054):
    if total <= 0:
        return (0.0, 0.0)
    p = wins / total
    denom = 1.0 + z * z / total
    centre = p + z * z / (2.0 * total)
    radius = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * total)) / total)
    return ((centre - radius) / denom, (centre + radius) / denom)


def run_pipeline(rows, rt: dict, live_parity: str | None):
    qualified = rt["filter_proof_qualified"](rows)
    print(f"\n[1] proof-qualified: {len(qualified)}/{len(rows)} rows")
    if not qualified:
        print("BLOQUEO: 0 proof-qualified rows.")
        return 2
    symbols = {}
    for r in qualified:
        symbols[str(r.get("symbol"))] = symbols.get(str(r.get("symbol")), 0) + 1
    if len(symbols) > 1:
        top = max(symbols.items(), key=lambda kv: kv[1])[0]
        print(f"    multiple symbols {symbols} -> scoring majority symbol {top}")
        qualified = [r for r in qualified if str(r.get("symbol")) == top]
    cohort = rt["independent_1h_cohort"](qualified)
    n = len(cohort)
    wins = sum(1 for r in cohort if r.get("outcome") == "WIN")
    losses = n - wins
    longs = [r for r in cohort if str(r.get("prediction")).upper() == "LONG"]
    shorts = [r for r in cohort if str(r.get("prediction")).upper() == "SHORT"]
    valid_conf = [r for r in cohort if rt["_confidence"](r) is not None]
    print(f"[2] independent 1h cohort: n={n}  LONG={len(longs)} SHORT={len(shorts)}")
    lw = sum(1 for r in longs if r.get("outcome") == "WIN")
    sw = sum(1 for r in shorts if r.get("outcome") == "WIN")
    print(f"    LONG  {lw}/{len(longs)}  ({100.0 * lw / max(len(longs), 1):.2f}%)")
    print(f"    SHORT {sw}/{len(shorts)}  ({100.0 * sw / max(len(shorts), 1):.2f}%)")
    print(f"[3] global {wins}W/{losses}L = {100.0 * wins / max(n, 1):.2f}%  "
          f"wilson_lower_95 = {rt['_wilson_lower'](wins, n):.6f}")
    print(f"    valid_confidence = {len(valid_conf)}")

    if live_parity:
        ln, lwv, llv, lvc = (int(x) for x in live_parity.split(","))
        parity_failures = []
        for name, live, got in (("n", ln, n), ("wins", lwv, wins),
                                ("losses", llv, losses), ("valid_conf", lvc, len(valid_conf))):
            delta = abs(got - live) / max(abs(live), 1)
            passed = delta <= 0.05
            flag = "PASS" if passed else "FAIL — DESVIACION>5%"
            print(f"[4] paridad {name}: vivo={live} export={got} ({flag})")
            if not passed:
                parity_failures.append(name)
        if parity_failures:
            print("\\nBLOQUEO: PARITY_FAIL — " + ", ".join(parity_failures) +
                  "; no se emite veredicto de aceptación.")
            return 3

    if n < 100:
        print("\nBLOQUEO: n<100 — insuficiente para Fase 1 (minimo pre-declarado).")
        return 2

    cal = valid_conf
    split = int(len(cal) * 0.67)
    train, holdout = cal[:split], cal[split:]
    print(f"\n[5] split cronologico 67/33 sobre valid_confidence: "
          f"train={len(train)} holdout={len(holdout)}")
    if len(holdout) < 50:
        print("BLOQUEO: holdout<50 — esperar mas filas resueltas.")
        return 2

    def xy(rs):
        return ([int(r.get("outcome") == "WIN") for r in rs],
                [float(rt["_confidence"](r)) for r in rs])

    tr_y, tr_p = xy(train)
    ho_y, ho_p = xy(holdout)
    import numpy as np

    platt = rt["PlattCalibrator"]().fit(np.asarray(tr_y), np.asarray(tr_p))
    print(f"[6] Platt recalibracion (train-only): a={platt.a:+.4f} b={platt.b:+.4f} "
          f"(a=1,b=0 seria 'ya calibrado')")

    ho_cal = platt.predict(np.asarray(ho_p))
    brier_raw = rt["brier_score"](np.asarray(ho_y), np.asarray(ho_p))
    brier_cal = rt["brier_score"](np.asarray(ho_y), ho_cal)
    base = sum(tr_y) / len(tr_y)
    brier_base = rt["brier_score"](np.asarray(ho_y), np.asarray([base] * len(ho_y)))
    ece_raw = rt["expected_calibration_error"](np.asarray(ho_y), np.asarray(ho_p))
    ece_cal = rt["expected_calibration_error"](np.asarray(ho_y), ho_cal)
    print(f"\n[7] holdout metrics:")
    print(f"    brier_raw={brier_raw:.4f}  brier_recal={brier_cal:.4f}  brier_base={brier_base:.4f}")
    print(f"    ece10_raw={ece_raw:.4f}  ece10_recal={ece_cal:.4f}")

    # G3: top vs bottom confidence terciles on the holdout
    order = sorted(range(len(ho_p)), key=lambda i: ho_p[i])
    third = max(1, len(order) // 3)
    lo_idx, hi_idx = order[:third], order[-third:]
    lo_w = sum(ho_y[i] for i in lo_idx)
    hi_w = sum(ho_y[i] for i in hi_idx)
    gap_pts = 100.0 * (hi_w / len(hi_idx) - lo_w / len(lo_idx))
    lo_int = _wilson_interval(lo_w, len(lo_idx))
    hi_int = _wilson_interval(hi_w, len(hi_idx))
    print(f"    G3 buckets: bottom {lo_w}/{len(lo_idx)} ({100.0 * lo_w / len(lo_idx):.1f}%) "
          f"vs top {hi_w}/{len(hi_idx)} ({100.0 * hi_w / len(hi_idx):.1f}%) "
          f"-> gap {gap_pts:+.1f}pts; Wilson95 lo=({lo_int[0]:.3f},{lo_int[1]:.3f}) "
          f"hi=({hi_int[0]:.3f},{hi_int[1]:.3f})")

    g1 = brier_cal < brier_base and brier_cal <= 0.25
    g2 = ece_cal <= 0.05
    g3 = gap_pts >= 5.0 and hi_int[0] > lo_int[1]
    print(f"\n[8] COMPUTERTAS:")
    print(f"    G1 Brier recal < base-rate y <= 0.25 : {'PASS' if g1 else 'FAIL'} "
          f"({brier_cal:.4f} vs {brier_base:.4f})")
    print(f"    G2 ECE10 recal <= 0.05               : {'PASS' if g2 else 'FAIL'} ({ece_cal:.4f})")
    print(f"    G3 bucket discriminacion >= 5pts s/cruce: {'PASS' if g3 else 'FAIL'} ({gap_pts:+.1f}pts)")
    verdict = "PASS" if (g1 and g2 and g3) else "FAIL"
    print(f"\n[9] VEREDICTO FASE 1 (tecnico): {verdict}")
    print("    DISCLAIMER: una corrida sintetica/REGENERATED pasando las compuertas")
    print("    es reproducibilidad tecnica, NO validacion cientifica. La validacion")
    print("    cientifica requiere el seal autentico de Northflank. Hasta entonces:")
    print("    AUTHORITY_SCORE=REJECTED, EDGE=UNPROVEN.")
    return 0 if (g1 and g2 and g3) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="SENEX Fase 1 calibration harness; exact seal identity is mandatory")
    ap.add_argument("evidence", help="authentic authority seal .json; unsealed CSV/JSONL cannot yield a verdict")
    ap.add_argument("--expect-commit", required=True, help="40-hex source_commit from fresh independent runtime readback")
    ap.add_argument("--expect-tree", required=True, help="40-hex source_tree from fresh independent runtime readback")
    ap.add_argument("--expect-build-digest", required=True, help="sha256:<64-hex> build_digest from fresh independent runtime readback")
    ap.add_argument("--live-parity", default=None, help="n,wins,losses,valid_conf from live freeze")
    ap.add_argument("--backend-dir", default=None, help="senecio_polymarket dir containing backend/")
    args = ap.parse_args()

    if not args.evidence.endswith(".json"):
        print("BLOQUEO: un CSV/JSONL no autentica la identidad del productor; no se emite veredicto.")
        return 2
    if not re.fullmatch(r"[0-9a-f]{40}", args.expect_commit):
        print("BLOQUEO: --expect-commit debe ser un SHA de 40 hex obtenido por readback independiente.")
        return 2
    if not re.fullmatch(r"[0-9a-f]{40}", args.expect_tree):
        print("BLOQUEO: --expect-tree debe ser un SHA de árbol de 40 hex obtenido por readback independiente.")
        return 2
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.expect_build_digest):
        print("BLOQUEO: --expect-build-digest debe tener formato sha256:<64 hex> y proceder de readback independiente.")
        return 2

    backend = _resolve_backend(args.backend_dir)
    print(f"[REGENERATED v2.1 2026-10-20] backend: {backend}")
    rt = _import_runtime(backend)
    rows = load_rows(args.evidence, rt, args.expect_commit, args.expect_tree, args.expect_build_digest)
    if rows is None:
        return 2
    return run_pipeline(rows, rt, args.live_parity)


if __name__ == "__main__":
    raise SystemExit(main())
