"""
ACT-XXIX — Coordinator: AntiFragilityCoordinator
=================================================

Ties together all 8 anti-fragility modules into one orchestrator that the
main FastAPI app can mount with a single line.

The coordinator exposes a unified snapshot endpoint that returns the state
of every subsystem, plus domain-specific helpers used by the new API
endpoints in main.py.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from .event_sourcing import (
    EventStore, GlobalAuditLedger, SnapshotManager, DeterministicReplayer,
    PredictionLifecycleAggregate, GENESIS_HASH,
)
from .invariant_checker import (
    InvariantRegistry, HashChainInvariant, StateMachineValidator,
    DependencyGraphValidator, CorruptionDetector,
    RangeInvariant, Severity,
)
from .lineage import (
    LineageGraph, PredictionAncestry, SchemaVersioner, SchemaVersion,
)
from .diagnostics import SelfDiagnostics, HealthScorer
from .resilience import (
    ResilienceCoordinator, CheckpointManager, CrashRecovery,
    BackgroundIntegrityVerifier,
)
from .reproducibility import (
    DeterministicSeed, ExperimentRegistry, ReproducibilityReport,
    CrossValidationRegistry, BenchmarkSuite, Benchmark,
)
from .market_simulation import (
    SyntheticMarketGenerator, ScenarioGenerator, AdversarialMarketSimulator,
    RegimeTransitionSimulator, FaultInjector, TimeTravelReplayEngine,
)
from .architecture_validator import (
    ArchitectureValidator, build_senecio_architecture_spec,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class AntiFragilityCoordinator:
    """One-stop orchestrator for the ACT-XXIX anti-fragility stack.

    Wires up:
      - GlobalAuditLedger (hash-chained event log)
      - SnapshotManager (state snapshots)
      - InvariantRegistry (with built-in hash-chain invariant)
      - StateMachineValidator (for prediction lifecycle)
      - DependencyGraphValidator (module dependency DAG)
      - CorruptionDetector (hash + schema)
      - LineageGraph + PredictionAncestry
      - SchemaVersioner (with v1→v2 prediction schema registered)
      - SelfDiagnostics
      - ResilienceCoordinator (breakers + checkpoints + watchdog + BIV)
      - DeterministicSeed + ExperimentRegistry + ReproducibilityReport
      - CrossValidationRegistry
      - BenchmarkSuite
      - SyntheticMarketGenerator + ScenarioGenerator + AdversarialMarketSimulator
        + RegimeTransitionSimulator + FaultInjector + TimeTravelReplayEngine
      - ArchitectureValidator (with pre-built SENECIO spec)

    Lifecycle:
      coord = AntiFragilityCoordinator()
      coord.start()      # starts background integrity verifier
      ...
      coord.stop()       # stops BIV
    """

    def __init__(self,
                 ledger_path: str = "data/antifragility/audit_ledger.jsonl",
                 snapshot_dir: str = "data/antifragility/snapshots",
                 checkpoint_dir: str = "data/antifragility/checkpoints",
                 experiment_dir: str = "data/antifragility/experiments",
                 cv_dir: str = "data/antifragility/cv_runs",
                 benchmark_dir: str = "data/antifragility/benchmarks",
                 root_seed: int = 42,
                 start_biv: bool = False):
        # Event sourcing
        self.ledger = GlobalAuditLedger(ledger_path)
        self.snapshots = SnapshotManager(snapshot_dir)
        self.replayer = DeterministicReplayer(self.ledger.store)

        # Invariants
        self.invariants = InvariantRegistry()
        self.invariants.register(HashChainInvariant(
            "audit_ledger_integrity", self.ledger.store,
            severity=Severity.CRITICAL, tags=("integrity",),
        ))
        self.state_machine = StateMachineValidator(
            name="prediction_lifecycle")
        self._init_state_machine()
        self.dep_graph = DependencyGraphValidator(name="module_deps")
        self.corruption = CorruptionDetector()

        # Lineage
        self.lineage = LineageGraph(name="senecio_lineage")
        self.prediction_ancestry = PredictionAncestry(self.lineage)
        self.schema_versioner = SchemaVersioner()
        self._init_schemas()

        # Diagnostics
        self.diagnostics = SelfDiagnostics()

        # Resilience
        self.resilience = ResilienceCoordinator(
            checkpoint_dir=checkpoint_dir,
            verification_fn=self._biv_check,
            verification_interval_s=60.0,
        )

        # Reproducibility
        self.seed = DeterministicSeed(root_seed=root_seed)
        self.experiments = ExperimentRegistry(experiment_dir)
        self.repro_report = ReproducibilityReport(self.experiments, self.seed)
        self.cv_registry = CrossValidationRegistry(cv_dir)
        self.benchmarks = BenchmarkSuite(benchmark_dir)

        # Market simulation
        self.market_gen = SyntheticMarketGenerator(seed=root_seed)
        self.scenario_gen = ScenarioGenerator(self.market_gen)
        self.adversarial = AdversarialMarketSimulator(seed=root_seed)
        self.regime_sim = RegimeTransitionSimulator(self.market_gen)
        self.fault_injector = FaultInjector()
        self.time_travel = TimeTravelReplayEngine()

        # Architecture
        self.arch_spec = build_senecio_architecture_spec()
        self.arch_validator = ArchitectureValidator(self.arch_spec)

        # Internal
        self._lock = threading.RLock()
        self._started = False

        if start_biv:
            self.start()

    def _init_state_machine(self) -> None:
        """Standard prediction lifecycle states + transitions."""
        for s in ("IDLE", "PROPOSED", "FEATURED", "SIGNALLED",
                  "ROUTED", "EXECUTED", "VERIFIED", "CLOSED", "EXPIRED"):
            self.state_machine.add_state(s)
        self.state_machine.add_transition("IDLE", "PROPOSED")
        self.state_machine.add_transition("PROPOSED", "FEATURED")
        self.state_machine.add_transition("PROPOSED", "EXPIRED")
        self.state_machine.add_transition("FEATURED", "SIGNALLED")
        self.state_machine.add_transition("FEATURED", "EXPIRED")
        self.state_machine.add_transition("SIGNALLED", "ROUTED")
        self.state_machine.add_transition("SIGNALLED", "EXPIRED")
        self.state_machine.add_transition("ROUTED", "EXECUTED")
        self.state_machine.add_transition("ROUTED", "EXPIRED")
        self.state_machine.add_transition("EXECUTED", "CLOSED")
        self.state_machine.add_transition("EXECUTED", "VERIFIED")
        self.state_machine.add_transition("VERIFIED", "CLOSED")
        self.state_machine.add_transition("CLOSED", "IDLE")  # reset
        self.state_machine.add_transition("EXPIRED", "IDLE")

    def _init_schemas(self) -> None:
        """Register known schema versions."""
        # Prediction v1
        self.schema_versioner.register(SchemaVersion(
            kind="prediction",
            version=1,
            schema={
                "type": "object",
                "required": ["prediction_id", "symbol", "direction", "confidence"],
                "properties": {
                    "prediction_id": {"type": "string"},
                    "symbol": {"type": "string"},
                    "direction": {"type": "string"},
                    "confidence": {"type": "number", "min": 0, "max": 1},
                },
            },
            introduced_at="2026-01-01T00:00:00Z",
        ))
        # Prediction v2 — adds outcome + realized_return
        self.schema_versioner.register(SchemaVersion(
            kind="prediction",
            version=2,
            schema={
                "type": "object",
                "required": ["prediction_id", "symbol", "direction",
                              "confidence", "ts"],
                "properties": {
                    "prediction_id": {"type": "string"},
                    "symbol": {"type": "string"},
                    "direction": {"type": "string"},
                    "confidence": {"type": "number", "min": 0, "max": 1},
                    "ts": {"type": "string"},
                    "outcome": {"type": "string"},
                    "realized_return": {"type": "number"},
                },
            },
            introduced_at="2026-06-01T00:00:00Z",
            migrate_from=1,
        ))

        def migrator_v1_v2(old: dict) -> dict:
            new = dict(old)
            new.setdefault("ts", _now_iso())
            return new
        self.schema_versioner._migrators[("prediction", 1, 2)] = migrator_v1_v2

    def _biv_check(self) -> dict:
        """Background integrity verifier check."""
        ok, broken = self.ledger.verify_integrity()
        return {
            "ts": _now_iso(),
            "ok": ok,
            "broken_seqs": broken[:10],
            "event_count": self.ledger.count(),
        }

    # -----------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self.resilience.start_biv()
            self._started = True

    def stop(self) -> None:
        with self._lock:
            if not self._started:
                return
            self.resilience.stop_biv()
            self.fault_injector.stop()
            self._started = False

    # -----------------------------------------------------------------
    # Domain helpers
    # -----------------------------------------------------------------

    def record_prediction_lifecycle(self, prediction_id: str,
                                     events: list[tuple[str, dict]]) -> None:
        """Record a sequence of (event_type, payload) tuples for one prediction."""
        for ev_type, payload in events:
            full_payload = dict(payload)
            full_payload["prediction_id"] = prediction_id
            self.ledger.store.append(ev_type, full_payload)

    def get_prediction_ancestry(self, prediction_id: str) -> dict:
        """Returns the prediction ancestry explanation."""
        return self.prediction_ancestry.explain(prediction_id)

    def checkpoint_subsystem(self, name: str, state: dict) -> dict:
        cp = self.resilience.checkpoints.take_snapshot(name, state)
        return cp.to_dict()

    def run_invariants(self) -> list[dict]:
        results = self.invariants.run_all()
        return [r.to_dict() for r in results]

    def run_diagnostics(self, features: dict | None = None,
                       member_predictions: list[float] | None = None,
                       sample_vector: list[float] | None = None) -> dict:
        return self.diagnostics.run(
            features=features,
            member_predictions=member_predictions,
            sample_vector=sample_vector,
        )

    def run_architecture_validation(self) -> dict:
        report = self.arch_validator.validate()
        return report.to_dict()

    def register_experiment(self, name: str, kind: str, params: dict,
                            metrics: dict, artifacts: list[str] | None = None,
                            notes: str = "") -> dict:
        from .reproducibility import Experiment
        seed_val = self.seed.derive(name)
        exp = Experiment.create(
            name=name, kind=kind, params=params, metrics=metrics,
            artifacts=artifacts or [], seed=seed_val, notes=notes,
        )
        self.experiments.register(exp)
        return exp.to_dict()

    def get_reproducibility_report(self, experiment_id: str) -> dict:
        return self.repro_report.generate(experiment_id)

    def run_benchmarks(self) -> list[dict]:
        results = self.benchmarks.run_all()
        return [r.to_dict() for r in results]

    def generate_synthetic_bars(self, n: int = 100) -> list[dict]:
        bars = self.market_gen.generate(n)
        return [b.to_dict() for b in bars]

    def generate_scenarios(self) -> dict[str, dict]:
        scenarios = self.scenario_gen.all_scenarios()
        return {k: v.to_dict() for k, v in scenarios.items()}

    def inject_fault(self, kind: str, **kwargs) -> dict:
        """Inject a fault of the given kind. Returns the fault descriptor."""
        from .market_simulation import (
            ExchangeFailureSimulator, NetworkDegradationSimulator,
            APIInconsistencySimulator, ClockSkewSimulator,
        )
        if kind == "exchange_outage":
            fault = ExchangeFailureSimulator.outage(**kwargs)
        elif kind == "partial_fill":
            fault = ExchangeFailureSimulator.partial_fill_rate(**kwargs)
        elif kind == "rejected_orders":
            fault = ExchangeFailureSimulator.rejected_orders(**kwargs)
        elif kind == "latency_spike":
            fault = NetworkDegradationSimulator.latency_spike(**kwargs)
        elif kind == "packet_loss":
            fault = NetworkDegradationSimulator.packet_loss(**kwargs)
        elif kind == "desync":
            fault = NetworkDegradationSimulator.desync(**kwargs)
        elif kind == "stale_quotes":
            fault = APIInconsistencySimulator.stale_quotes(**kwargs)
        elif kind == "wrong_symbol":
            fault = APIInconsistencySimulator.wrong_symbol(**kwargs)
        elif kind == "schema_drift":
            fault = APIInconsistencySimulator.schema_drift(**kwargs)
        elif kind == "time_jump":
            fault = ClockSkewSimulator.time_jump(**kwargs)
        elif kind == "drift":
            fault = ClockSkewSimulator.drift(**kwargs)
        else:
            raise ValueError(f"unknown fault kind: {kind}")
        self.fault_injector.schedule_fault(fault, delay_s=0.0)
        return fault.to_dict()

    def active_faults(self) -> list[dict]:
        return [f.to_dict() for f in self.fault_injector.active_faults()]

    # -----------------------------------------------------------------
    # Snapshot — used by /api/antifragility/state
    # -----------------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "ts": _now_iso(),
                "version": "ACT-XXIX-systemic-antifragility",
                "started": self._started,
                "ledger": {
                    "event_count": self.ledger.count(),
                    "last_hash": self.ledger.store.last_hash[:12] + "...",
                    "integrity_ok": self.ledger.verify_integrity()[0],
                },
                "snapshots": {
                    "total": self.snapshots.count(),
                },
                "invariants": self.invariants.summary(),
                "state_machine": self.state_machine.to_dict(),
                "dependency_graph": self.dep_graph.to_dict(),
                "lineage": {
                    "nodes": self.lineage.node_count(),
                    "edges": self.lineage.edge_count(),
                },
                "schema_versions": {
                    kind: self.schema_versioner.list_versions(kind)
                    for kind in ("prediction",)
                },
                "diagnostics": self.diagnostics.health.compute(),
                "resilience": self.resilience.snapshot(),
                "experiments": {
                    "total": self.experiments.count(),
                    "latest": [e.to_dict() for e in self.experiments.latest(n=5)],
                },
                "cv_registry": {
                    "total_runs": self.cv_registry.count(),
                },
                "benchmarks": {
                    "registered": len(self.benchmarks.list_benchmarks()),
                    "history_size": len(self.benchmarks.history()),
                },
                "market_sim": self.market_gen.stats(),
                "fault_injection": {
                    "active_count": len(self.fault_injector.active_faults()),
                    "history_size": len(self.fault_injector.history()),
                },
                "time_travel": {
                    "bars_loaded": self.time_travel.count(),
                    "time_range": self.time_travel.time_range(),
                },
            }


__all__ = ["AntiFragilityCoordinator"]
