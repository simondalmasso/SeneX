"""
ACT-XXIX — Module 6: Reproducibility — Seeds, Experiments, Benchmarks
=====================================================================

Ensures every research artifact is reproducible. Every experiment has a
deterministic seed, every CV run is registered, every benchmark result is
tracked over time.

Public surface
--------------
- ``DeterministicSeed``   — hierarchical deterministic seed manager
- ``Experiment``          — frozen experiment record
- ``ExperimentRegistry``  — registry with hash-based lookup
- ``ReproducibilityReport``— regenerates a report for an experiment
- ``CVRun``               — one cross-validation run
- ``CrossValidationRegistry``— tracks CV runs + outcomes
- ``Benchmark``           — named benchmark definition
- ``BenchmarkSuite``      — collection + runner
- ``BenchmarkResult``     — one run's result
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _hash_obj(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# DeterministicSeed
# ---------------------------------------------------------------------------

class DeterministicSeed:
    """Hierarchical deterministic seed manager.

    Usage:
        ds = DeterministicSeed(root_seed=42)
        seed_a = ds.derive("portfolio_engine")
        seed_b = ds.derive("research.calibration")
        # Same root + same path → same derived seed, always
        ds2 = DeterministicSeed(root_seed=42)
        assert ds2.derive("portfolio_engine") == seed_a
    """

    def __init__(self, root_seed: int = 42):
        self.root_seed = int(root_seed)

    def derive(self, path: str) -> int:
        """Derive a sub-seed from a hierarchical path.

        Path components are dot-separated (e.g. "research.calibration.platt").
        Each component contributes a SHA-256 mix into the seed.
        """
        h = hashlib.sha256()
        h.update(str(self.root_seed).encode("utf-8"))
        h.update(b"|")
        h.update(path.encode("utf-8"))
        # Use first 8 bytes as a uint64, masked to int32 for portability
        return int.from_bytes(h.digest()[:8], "big") & 0x7FFFFFFF

    def derive_sequence(self, path: str, n: int) -> list[int]:
        """Derive n distinct sub-seeds under a path (e.g. for ensemble members)."""
        return [self.derive(f"{path}.{i}") for i in range(n)]

    def get_rng(self, path: str) -> np.random.Generator:
        """Returns a numpy Generator seeded with the derived seed."""
        return np.random.default_rng(self.derive(path))

    def fingerprint(self) -> str:
        """Returns a stable hash identifying this seed config."""
        return _hash_obj({"root_seed": self.root_seed})

    def to_dict(self) -> dict:
        return {
            "root_seed": self.root_seed,
            "fingerprint": self.fingerprint(),
        }


# ---------------------------------------------------------------------------
# Experiment + ExperimentRegistry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Experiment:
    """Frozen experiment record. ``hash`` is content-derived."""
    experiment_id: str
    name: str
    kind: str                 # "backtest", "cv", "montecarlo", "wfo", etc.
    params: dict
    metrics: dict
    artifacts: list[str]      # file paths
    seed: int
    started_at: str
    finished_at: str
    duration_s: float
    hash: str
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def create(cls, name: str, kind: str, params: dict, metrics: dict,
               artifacts: list[str] | None = None, seed: int = 0,
               started_at: str = "", finished_at: str = "",
               duration_s: float = 0.0, notes: str = "") -> "Experiment":
        started_at = started_at or _now_iso()
        finished_at = finished_at or _now_iso()
        experiment_id = _hash_obj({
            "name": name, "kind": kind, "params": params, "seed": seed,
        })[:16]
        h = _hash_obj({
            "experiment_id": experiment_id,
            "name": name, "kind": kind,
            "params": params, "metrics": metrics,
            "artifacts": artifacts or [], "seed": seed,
            "started_at": started_at, "finished_at": finished_at,
            "duration_s": duration_s,
        })
        return cls(
            experiment_id=experiment_id,
            name=name, kind=kind, params=params, metrics=metrics,
            artifacts=artifacts or [], seed=seed,
            started_at=started_at, finished_at=finished_at,
            duration_s=duration_s, hash=h, notes=notes,
        )


class ExperimentRegistry:
    """Registry of experiments with disk persistence + query helpers.

    File format: one JSON per line under ``registry_dir/experiments.jsonl``.
    """

    def __init__(self, registry_dir: str | Path = "data/antifragility/experiments"):
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.registry_dir / "experiments.jsonl"
        self._experiments: dict[str, Experiment] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.registry_file.exists():
            return
        with self._lock:
            for line in self.registry_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    exp = Experiment(
                        experiment_id=d["experiment_id"],
                        name=d["name"], kind=d["kind"],
                        params=d.get("params", {}),
                        metrics=d.get("metrics", {}),
                        artifacts=d.get("artifacts", []),
                        seed=d.get("seed", 0),
                        started_at=d.get("started_at", ""),
                        finished_at=d.get("finished_at", ""),
                        duration_s=d.get("duration_s", 0.0),
                        hash=d.get("hash", ""),
                        notes=d.get("notes", ""),
                    )
                    self._experiments[exp.experiment_id] = exp
                except (json.JSONDecodeError, KeyError):
                    continue

    def register(self, exp: Experiment) -> Experiment:
        with self._lock:
            self._experiments[exp.experiment_id] = exp
            with self.registry_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(exp.to_dict(), default=str) + "\n")
        return exp

    def get(self, experiment_id: str) -> Experiment | None:
        with self._lock:
            return self._experiments.get(experiment_id)

    def find_by_name(self, name: str) -> list[Experiment]:
        with self._lock:
            return [e for e in self._experiments.values() if e.name == name]

    def find_by_kind(self, kind: str) -> list[Experiment]:
        with self._lock:
            return [e for e in self._experiments.values() if e.kind == kind]

    def latest(self, kind: str | None = None, n: int = 10) -> list[Experiment]:
        with self._lock:
            exps = list(self._experiments.values())
        if kind is not None:
            exps = [e for e in exps if e.kind == kind]
        exps.sort(key=lambda e: e.finished_at, reverse=True)
        return exps[:n]

    def all(self) -> list[Experiment]:
        with self._lock:
            return list(self._experiments.values())

    def count(self) -> int:
        with self._lock:
            return len(self._experiments)

    def verify(self, experiment_id: str) -> bool:
        """Re-derive hash and compare."""
        exp = self.get(experiment_id)
        if exp is None:
            return False
        h = _hash_obj({
            "experiment_id": exp.experiment_id,
            "name": exp.name, "kind": exp.kind,
            "params": exp.params, "metrics": exp.metrics,
            "artifacts": exp.artifacts, "seed": exp.seed,
            "started_at": exp.started_at, "finished_at": exp.finished_at,
            "duration_s": exp.duration_s,
        })
        return h == exp.hash


# ---------------------------------------------------------------------------
# ReproducibilityReport
# ---------------------------------------------------------------------------

class ReproducibilityReport:
    """Generates a reproducibility report for an experiment.

    A reproducibility report contains:
      - The full experiment record
      - The seed fingerprint (so the same RNG state can be reconstructed)
      - All input parameters
      - All output metrics
      - All artifact paths (with hash verification if files exist)
      - Instructions for reproducing the experiment
    """

    def __init__(self, registry: ExperimentRegistry,
                 seed_manager: DeterministicSeed):
        self.registry = registry
        self.seed_manager = seed_manager

    def generate(self, experiment_id: str) -> dict:
        exp = self.registry.get(experiment_id)
        if exp is None:
            return {"error": f"experiment {experiment_id} not found"}
        # Verify hash
        hash_ok = self.registry.verify(experiment_id)
        # Verify artifacts
        artifact_reports = []
        for path in exp.artifacts:
            try:
                p = Path(path)
                if p.exists() and p.is_file():
                    file_hash = _hash_obj(p.read_bytes())
                    artifact_reports.append({
                        "path": path,
                        "exists": True,
                        "size_bytes": p.stat().st_size,
                        "hash": file_hash,
                    })
                else:
                    artifact_reports.append({
                        "path": path,
                        "exists": False,
                    })
            except Exception as e:
                artifact_reports.append({
                    "path": path,
                    "error": str(e),
                })
        return {
            "experiment": exp.to_dict(),
            "hash_verified": hash_ok,
            "seed_fingerprint": self.seed_manager.fingerprint(),
            "derived_seed": self.seed_manager.derive(exp.name),
            "artifacts": artifact_reports,
            "reproduction_instructions": (
                f"1. Initialise DeterministicSeed(root_seed={self.seed_manager.root_seed})\n"
                f"2. Call seed_manager.derive({exp.name!r}) to get the same RNG seed\n"
                f"3. Re-run experiment with params: {json.dumps(exp.params, sort_keys=True)}\n"
                f"4. Compare output metrics to: {json.dumps(exp.metrics, sort_keys=True)}"
            ),
            "generated_at": _now_iso(),
        }


# ---------------------------------------------------------------------------
# CVRun + CrossValidationRegistry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CVRun:
    """One cross-validation run record."""
    run_id: str
    experiment_id: str       # links back to parent experiment
    fold_idx: int
    train_size: int
    test_size: int
    metrics: dict
    started_at: str
    finished_at: str
    hash: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def create(cls, experiment_id: str, fold_idx: int,
               train_size: int, test_size: int, metrics: dict,
               started_at: str = "", finished_at: str = "") -> "CVRun":
        started_at = started_at or _now_iso()
        finished_at = finished_at or _now_iso()
        run_id = _hash_obj({
            "experiment_id": experiment_id, "fold_idx": fold_idx,
            "started_at": started_at,
        })[:16]
        h = _hash_obj({
            "run_id": run_id, "experiment_id": experiment_id,
            "fold_idx": fold_idx, "train_size": train_size,
            "test_size": test_size, "metrics": metrics,
            "started_at": started_at, "finished_at": finished_at,
        })
        return cls(
            run_id=run_id, experiment_id=experiment_id, fold_idx=fold_idx,
            train_size=train_size, test_size=test_size, metrics=metrics,
            started_at=started_at, finished_at=finished_at, hash=h,
        )


class CrossValidationRegistry:
    """Tracks CV runs + aggregates per-experiment outcomes."""

    def __init__(self, registry_dir: str | Path = "data/antifragility/cv_runs"):
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.registry_file = self.registry_dir / "cv_runs.jsonl"
        self._runs: dict[str, CVRun] = {}
        self._by_experiment: dict[str, list[str]] = defaultdict(list)
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.registry_file.exists():
            return
        for line in self.registry_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                run = CVRun(
                    run_id=d["run_id"], experiment_id=d["experiment_id"],
                    fold_idx=d["fold_idx"], train_size=d["train_size"],
                    test_size=d["test_size"], metrics=d.get("metrics", {}),
                    started_at=d.get("started_at", ""),
                    finished_at=d.get("finished_at", ""),
                    hash=d.get("hash", ""),
                )
                self._runs[run.run_id] = run
                self._by_experiment[run.experiment_id].append(run.run_id)
            except (json.JSONDecodeError, KeyError):
                continue

    def register(self, run: CVRun) -> CVRun:
        with self._lock:
            self._runs[run.run_id] = run
            self._by_experiment[run.experiment_id].append(run.run_id)
            with self.registry_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(run.to_dict(), default=str) + "\n")
        return run

    def runs_for(self, experiment_id: str) -> list[CVRun]:
        with self._lock:
            ids = list(self._by_experiment.get(experiment_id, []))
            return [self._runs[rid] for rid in ids if rid in self._runs]

    def aggregate(self, experiment_id: str) -> dict:
        """Compute mean/std of each metric across folds."""
        runs = self.runs_for(experiment_id)
        if not runs:
            return {"experiment_id": experiment_id, "folds": 0}
        # Collect metric values
        metric_values: dict[str, list[float]] = defaultdict(list)
        for run in runs:
            for k, v in run.metrics.items():
                try:
                    metric_values[k].append(float(v))
                except (TypeError, ValueError):
                    continue
        summary = {}
        for k, vals in metric_values.items():
            arr = np.array(vals)
            summary[k] = {
                "mean": float(arr.mean()),
                "std": float(arr.std()),
                "min": float(arr.min()),
                "max": float(arr.max()),
                "n": len(arr),
            }
        return {
            "experiment_id": experiment_id,
            "folds": len(runs),
            "metrics": summary,
        }

    def count(self) -> int:
        with self._lock:
            return len(self._runs)


# ---------------------------------------------------------------------------
# Benchmark + BenchmarkSuite
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Benchmark:
    """Named benchmark definition."""
    name: str
    description: str
    fn: Callable[[], dict]   # returns metrics dict
    timeout_s: float = 60.0
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "timeout_s": self.timeout_s,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class BenchmarkResult:
    benchmark_name: str
    metrics: dict
    duration_s: float
    ts: str
    ok: bool
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class BenchmarkSuite:
    """Collection of benchmarks + runner with persistence.

    Each run persists to ``results_dir/benchmark_history.jsonl``.
    """

    def __init__(self, results_dir: str | Path = "data/antifragility/benchmarks"):
        self.results_dir = Path(results_dir)
        self.results_dir.mkdir(parents=True, exist_ok=True)
        self.history_file = self.results_dir / "benchmark_history.jsonl"
        self._benchmarks: dict[str, Benchmark] = {}
        self._history: deque[BenchmarkResult] = deque(maxlen=1000)
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.history_file.exists():
            return
        for line in self.history_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                result = BenchmarkResult(
                    benchmark_name=d["benchmark_name"],
                    metrics=d.get("metrics", {}),
                    duration_s=d.get("duration_s", 0.0),
                    ts=d.get("ts", ""),
                    ok=d.get("ok", True),
                    error=d.get("error", ""),
                )
                self._history.append(result)
            except (json.JSONDecodeError, KeyError):
                continue

    def register(self, benchmark: Benchmark) -> None:
        with self._lock:
            self._benchmarks[benchmark.name] = benchmark

    def run(self, name: str) -> BenchmarkResult:
        with self._lock:
            bench = self._benchmarks.get(name)
        if bench is None:
            raise KeyError(f"unknown benchmark: {name}")
        ts = _now_iso()
        t0 = time.time()
        try:
            metrics = bench.fn()
            duration = time.time() - t0
            result = BenchmarkResult(
                benchmark_name=name, metrics=metrics,
                duration_s=duration, ts=ts, ok=True,
            )
        except Exception as e:
            duration = time.time() - t0
            result = BenchmarkResult(
                benchmark_name=name, metrics={},
                duration_s=duration, ts=ts, ok=False, error=str(e),
            )
        with self._lock:
            self._history.append(result)
            with self.history_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(result.to_dict(), default=str) + "\n")
        return result

    def run_all(self) -> list[BenchmarkResult]:
        with self._lock:
            names = list(self._benchmarks.keys())
        return [self.run(n) for n in names]

    def run_by_tag(self, tag: str) -> list[BenchmarkResult]:
        with self._lock:
            names = [b.name for b in self._benchmarks.values()
                     if tag in b.tags]
        return [self.run(n) for n in names]

    def history(self, benchmark_name: str | None = None,
                limit: int = 100) -> list[BenchmarkResult]:
        with self._lock:
            results = list(self._history)
        if benchmark_name is not None:
            results = [r for r in results if r.benchmark_name == benchmark_name]
        return results[-limit:]

    def trend(self, benchmark_name: str,
              metric_name: str,
              limit: int = 50) -> list[dict]:
        """Returns [{ts, value}] for a specific metric over time."""
        results = self.history(benchmark_name, limit=limit)
        out = []
        for r in results:
            if metric_name in r.metrics:
                try:
                    out.append({
                        "ts": r.ts,
                        "value": float(r.metrics[metric_name]),
                        "ok": r.ok,
                    })
                except (TypeError, ValueError):
                    continue
        return out

    def list_benchmarks(self) -> list[dict]:
        with self._lock:
            return [b.to_dict() for b in self._benchmarks.values()]


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "DeterministicSeed",
    "Experiment",
    "ExperimentRegistry",
    "ReproducibilityReport",
    "CVRun",
    "CrossValidationRegistry",
    "Benchmark",
    "BenchmarkResult",
    "BenchmarkSuite",
]
