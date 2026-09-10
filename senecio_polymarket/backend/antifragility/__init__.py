"""
SENECIO ORACLE — ACT-XXIX: Systemic Anti-Fragility Layer
=========================================================

Public API for the antifragility subpackage. All modules are STRICT_ADDITIVE —
they do not modify any existing module.

Modules
-------
- event_sourcing          — hash-chained event sourcing + replay + snapshots
- invariant_checker       — invariants + state machine + DAG + corruption
- lineage                 — data lineage + schema versioning + provenance
- diagnostics             — health scoring + confidence decomp + anomalies
- resilience              — circuit breakers + checkpoints + watchdogs
- reproducibility         — seeds + experiments + CV registry + benchmarks
- market_simulation       — synthetic markets + chaos + fault injection
- architecture_validator  — architecture consistency validation
- coordinator             — top-level orchestrator
"""
from __future__ import annotations

# Module 1: event sourcing
from .event_sourcing import (
    Event, Snapshot, EventStore, GlobalAuditLedger,
    SnapshotManager, EventSourcedAggregate,
    PredictionLifecycleAggregate, DeterministicReplayer,
    GENESIS_HASH,
)
# Module 2: invariant checker
from .invariant_checker import (
    Severity, InvariantResult, Invariant,
    RangeInvariant, HashChainInvariant, SchemaInvariant, ThresholdInvariant,
    InvariantRegistry, StateMachineValidator, DependencyGraphValidator,
    CorruptionDetector, RuntimeAssertions,
)
# Module 3: lineage
from .lineage import (
    EdgeType, LineageNode, LineageEdge, LineageGraph,
    PredictionAncestry, DecisionProvenanceGraph,
    SchemaVersion, SchemaVersioner,
)
# Module 4: diagnostics
from .diagnostics import (
    MetricSample, HealthScorer, ConfidenceDecomposer,
    AnomalyClusterer, EnsembleDisagreementDetector, SelfDiagnostics,
)
# Module 5: resilience
from .resilience import (
    CircuitState, CircuitBreakerStats, CircuitBreaker, CircuitOpenError,
    CircuitBreakerRegistry, Checkpoint, CheckpointManager,
    CrashRecovery, ResourceWatchdog, BackgroundIntegrityVerifier,
    ResilienceCoordinator,
)
# Module 6: reproducibility
from .reproducibility import (
    DeterministicSeed, Experiment, ExperimentRegistry,
    ReproducibilityReport, CVRun, CrossValidationRegistry,
    Benchmark, BenchmarkResult, BenchmarkSuite,
)
# Module 7: market simulation
from .market_simulation import (
    OHLCVBar, Regime, SyntheticMarketGenerator,
    Scenario, ScenarioGenerator, AdversarialMarketSimulator,
    RegimeTransitionSimulator, Fault, FaultInjector,
    ExchangeFailureSimulator, NetworkDegradationSimulator,
    APIInconsistencySimulator, ClockSkewSimulator,
    TimeTravelReplayEngine,
)
# Module 8: architecture validator
from .architecture_validator import (
    FindingSeverity, ValidationFinding, InterfaceContract,
    ComponentDescriptor, ArchitectureSpec,
    ValidationReport, ArchitectureValidator,
    build_senecio_architecture_spec,
)
# Coordinator
from .coordinator import AntiFragilityCoordinator

VERSION = "ACT-XXIX-systemic-antifragility"

__all__ = [
    "VERSION",
    # M1
    "Event", "Snapshot", "EventStore", "GlobalAuditLedger",
    "SnapshotManager", "EventSourcedAggregate",
    "PredictionLifecycleAggregate", "DeterministicReplayer",
    "GENESIS_HASH",
    # M2
    "Severity", "InvariantResult", "Invariant",
    "RangeInvariant", "HashChainInvariant", "SchemaInvariant",
    "ThresholdInvariant", "InvariantRegistry",
    "StateMachineValidator", "DependencyGraphValidator",
    "CorruptionDetector", "RuntimeAssertions",
    # M3
    "EdgeType", "LineageNode", "LineageEdge", "LineageGraph",
    "PredictionAncestry", "DecisionProvenanceGraph",
    "SchemaVersion", "SchemaVersioner",
    # M4
    "MetricSample", "HealthScorer", "ConfidenceDecomposer",
    "AnomalyClusterer", "EnsembleDisagreementDetector", "SelfDiagnostics",
    # M5
    "CircuitState", "CircuitBreakerStats", "CircuitBreaker",
    "CircuitOpenError", "CircuitBreakerRegistry",
    "Checkpoint", "CheckpointManager", "CrashRecovery",
    "ResourceWatchdog", "BackgroundIntegrityVerifier",
    "ResilienceCoordinator",
    # M6
    "DeterministicSeed", "Experiment", "ExperimentRegistry",
    "ReproducibilityReport", "CVRun", "CrossValidationRegistry",
    "Benchmark", "BenchmarkResult", "BenchmarkSuite",
    # M7
    "OHLCVBar", "Regime", "SyntheticMarketGenerator",
    "Scenario", "ScenarioGenerator", "AdversarialMarketSimulator",
    "RegimeTransitionSimulator", "Fault", "FaultInjector",
    "ExchangeFailureSimulator", "NetworkDegradationSimulator",
    "APIInconsistencySimulator", "ClockSkewSimulator",
    "TimeTravelReplayEngine",
    # M8
    "FindingSeverity", "ValidationFinding", "InterfaceContract",
    "ComponentDescriptor", "ArchitectureSpec",
    "ValidationReport", "ArchitectureValidator",
    "build_senecio_architecture_spec",
    # Coordinator
    "AntiFragilityCoordinator",
]
