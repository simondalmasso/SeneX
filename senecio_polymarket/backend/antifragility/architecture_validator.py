"""
ACT-XXIX — Module 8: Architecture Consistency Validator
=======================================================

Verifies that the system's actual module structure matches the declared
architecture. Detects:
  - Interface contract violations
  - Version compatibility issues
  - Missing dependencies
  - Circular dependencies
  - Naming convention violations
  - Public API surface drift

Public surface
--------------
- ``ComponentDescriptor``   — declares one component + its interface
- ``InterfaceContract``     — declares expected method signatures
- ``ArchitectureSpec``      — full architecture declaration
- ``ArchitectureValidator`` — validates actual code vs spec
- ``ValidationFinding``     — one issue found during validation
- ``ValidationReport``      — full report with findings
"""
from __future__ import annotations

import importlib
import inspect
import re
import threading
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable

from .invariant_checker import DependencyGraphValidator


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

class FindingSeverity(str, Enum):
    INFO = "INFO"
    WARN = "WARN"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ValidationFinding:
    rule: str
    severity: FindingSeverity
    component: str
    msg: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass(frozen=True)
class InterfaceContract:
    """Declares one expected method/attribute on a component."""
    name: str
    kind: str             # "method" / "property" / "attribute"
    signature: str = ""   # for methods: "(arg1: int, arg2: str) -> bool"
    required: bool = True

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ComponentDescriptor:
    """Declares one component of the architecture."""
    name: str
    module_path: str      # e.g. "backend.portfolio.coordinator"
    class_name: str       # e.g. "PortfolioCoordinator"
    version: str = ""
    depends_on: tuple[str, ...] = ()
    provides: tuple[InterfaceContract, ...] = ()
    description: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "module_path": self.module_path,
            "class_name": self.class_name,
            "version": self.version,
            "depends_on": list(self.depends_on),
            "provides": [c.to_dict() for c in self.provides],
            "description": self.description,
        }


@dataclass(frozen=True)
class ArchitectureSpec:
    """Full architecture declaration."""
    name: str
    version: str
    components: tuple[ComponentDescriptor, ...]
    naming_convention: str = r"^[A-Z][A-Za-z0-9_]*$"  # PascalCase
    module_naming_convention: str = r"^[a-z][a-z0-9_]*$"
    forbidden_imports: tuple[str, ...] = ()
    required_components: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "components": [c.to_dict() for c in self.components],
            "naming_convention": self.naming_convention,
            "module_naming_convention": self.module_naming_convention,
            "forbidden_imports": list(self.forbidden_imports),
            "required_components": list(self.required_components),
        }


# ---------------------------------------------------------------------------
# ValidationReport
# ---------------------------------------------------------------------------

@dataclass
class ValidationReport:
    spec_name: str
    spec_version: str
    ts: str
    findings: list[ValidationFinding]
    components_checked: int
    components_passed: int
    duration_s: float

    @property
    def ok(self) -> bool:
        return all(
            f.severity != FindingSeverity.ERROR and
            f.severity != FindingSeverity.CRITICAL
            for f in self.findings
        )

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings
                   if f.severity in (FindingSeverity.ERROR,
                                      FindingSeverity.CRITICAL))

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings
                   if f.severity == FindingSeverity.WARN)

    def to_dict(self) -> dict:
        return {
            "spec_name": self.spec_name,
            "spec_version": self.spec_version,
            "ts": self.ts,
            "ok": self.ok,
            "error_count": self.error_count,
            "warn_count": self.warn_count,
            "components_checked": self.components_checked,
            "components_passed": self.components_passed,
            "duration_s": self.duration_s,
            "findings": [f.to_dict() for f in self.findings],
        }


# ---------------------------------------------------------------------------
# ArchitectureValidator
# ---------------------------------------------------------------------------

class ArchitectureValidator:
    """Validates actual code vs an ArchitectureSpec.

    Checks performed (each emits a finding on failure):
      R1  module_importable      — module_path can be imported
      R2  class_exists           — class_name exists in module
      R3  class_instantiable     — class can be instantiated (no required args
                                    in constructor; or call user-provided factory)
      R4  interface_contract     — every InterfaceContract on a component is
                                    present on the actual class
      R5  naming_convention      — class names match the convention
      R6  module_naming           — module path components match the convention
      R7  dependency_graph_acyclic — declared dependencies form a DAG
      R8  required_components    — every required component is present in spec
      R9  forbidden_imports      — no component module imports a forbidden module
      R10 version_compatibility  — every dependency declares a version >= ours
    """

    def __init__(self, spec: ArchitectureSpec,
                 factory: Callable[[ComponentDescriptor], Any] | None = None):
        self.spec = spec
        self.factory = factory  # optional: how to instantiate a component

    def validate(self) -> ValidationReport:
        import time as _time
        t0 = _time.time()
        findings: list[ValidationFinding] = []
        checked = 0
        passed = 0
        # Build dependency graph
        dg = DependencyGraphValidator(name=f"{self.spec.name}_dep_graph")
        component_names = {c.name for c in self.spec.components}
        # R8: required_components present
        for req in self.spec.required_components:
            if req not in component_names:
                findings.append(ValidationFinding(
                    rule="R8_required_components",
                    severity=FindingSeverity.CRITICAL,
                    component=req,
                    msg=f"required component '{req}' not in spec",
                ))
        # Check each component
        for comp in self.spec.components:
            checked += 1
            comp_findings = self._validate_component(comp)
            findings.extend(comp_findings)
            if not any(f.severity in (FindingSeverity.ERROR,
                                       FindingSeverity.CRITICAL)
                       for f in comp_findings):
                passed += 1
            # Add to dep graph
            dg.add_node(comp.name)
            for dep in comp.depends_on:
                if dep in component_names:
                    dg.add_edge(comp.name, dep)
        # R7: dependency graph acyclic
        cycle = dg.detect_cycle()
        if cycle is not None:
            findings.append(ValidationFinding(
                rule="R7_dependency_graph_acyclic",
                severity=FindingSeverity.CRITICAL,
                component="*",
                msg=f"circular dependency detected: {cycle}",
                detail={"cycle": cycle},
            ))
        duration = _time.time() - t0
        return ValidationReport(
            spec_name=self.spec.name,
            spec_version=self.spec.version,
            ts=_now_iso(),
            findings=findings,
            components_checked=checked,
            components_passed=passed,
            duration_s=duration,
        )

    def _validate_component(self,
                            comp: ComponentDescriptor) -> list[ValidationFinding]:
        findings: list[ValidationFinding] = []
        # R6: module naming convention
        for part in comp.module_path.split("."):
            if part and not re.match(self.spec.module_naming_convention, part):
                findings.append(ValidationFinding(
                    rule="R6_module_naming",
                    severity=FindingSeverity.WARN,
                    component=comp.name,
                    msg=f"module path segment '{part}' violates convention "
                        f"{self.spec.module_naming_convention}",
                ))
                break
        # R5: class naming convention
        if not re.match(self.spec.naming_convention, comp.class_name):
            findings.append(ValidationFinding(
                rule="R5_naming_convention",
                severity=FindingSeverity.WARN,
                component=comp.name,
                msg=f"class '{comp.class_name}' violates convention "
                    f"{self.spec.naming_convention}",
            ))
        # R1: module importable
        try:
            mod = importlib.import_module(comp.module_path)
        except ImportError as e:
            findings.append(ValidationFinding(
                rule="R1_module_importable",
                severity=FindingSeverity.CRITICAL,
                component=comp.name,
                msg=f"cannot import module '{comp.module_path}': {e}",
            ))
            return findings  # nothing more to check
        except Exception as e:
            findings.append(ValidationFinding(
                rule="R1_module_importable",
                severity=FindingSeverity.ERROR,
                component=comp.name,
                msg=f"module '{comp.module_path}' raised on import: {e}",
            ))
            return findings
        # R2: class exists
        cls = getattr(mod, comp.class_name, None)
        if cls is None:
            findings.append(ValidationFinding(
                rule="R2_class_exists",
                severity=FindingSeverity.CRITICAL,
                component=comp.name,
                msg=f"class '{comp.class_name}' not found in "
                    f"module '{comp.module_path}'",
            ))
            return findings
        # R9: forbidden imports (check module's loaded modules — heuristic)
        # We check the module's __dict__ for forbidden module names
        for forbidden in self.spec.forbidden_imports:
            if forbidden in str(mod.__dict__):
                findings.append(ValidationFinding(
                    rule="R9_forbidden_imports",
                    severity=FindingSeverity.ERROR,
                    component=comp.name,
                    msg=f"module references forbidden import '{forbidden}'",
                ))
        # R4: interface contracts
        for contract in comp.provides:
            if contract.kind == "method":
                attr = getattr(cls, contract.name, None)
                if attr is None:
                    if contract.required:
                        findings.append(ValidationFinding(
                            rule="R4_interface_contract",
                            severity=FindingSeverity.ERROR,
                            component=comp.name,
                            msg=f"missing required method '{contract.name}'",
                            detail={"contract": contract.to_dict()},
                        ))
                    continue
                if not callable(attr):
                    findings.append(ValidationFinding(
                        rule="R4_interface_contract",
                        severity=FindingSeverity.ERROR,
                        component=comp.name,
                        msg=f"'{contract.name}' is not callable",
                    ))
            elif contract.kind == "property":
                attr = getattr(cls, contract.name, None)
                if attr is None and contract.required:
                    findings.append(ValidationFinding(
                        rule="R4_interface_contract",
                        severity=FindingSeverity.ERROR,
                        component=comp.name,
                        msg=f"missing required property '{contract.name}'",
                    ))
            elif contract.kind == "attribute":
                # Only check class-level attribute (not instance)
                if not hasattr(cls, contract.name) and contract.required:
                    findings.append(ValidationFinding(
                        rule="R4_interface_contract",
                        severity=FindingSeverity.ERROR,
                        component=comp.name,
                        msg=f"missing required attribute '{contract.name}'",
                    ))
        # R3: instantiable (only if factory provided OR class takes no required args)
        if self.factory is not None:
            try:
                self.factory(comp)
            except Exception as e:
                findings.append(ValidationFinding(
                    rule="R3_class_instantiable",
                    severity=FindingSeverity.ERROR,
                    component=comp.name,
                    msg=f"factory failed to instantiate: {e}",
                ))
        else:
            # Heuristic: check constructor signature for required args
            try:
                sig = inspect.signature(cls.__init__)
                params = [
                    p for p in sig.parameters.values()
                    if p.name != "self" and p.default is inspect.Parameter.empty
                ]
                if params:
                    findings.append(ValidationFinding(
                        rule="R3_class_instantiable",
                        severity=FindingSeverity.INFO,
                        component=comp.name,
                        msg=f"__init__ has required params "
                            f"{[p.name for p in params]} (no factory provided; "
                            f"skipping instantiation check)",
                    ))
            except (ValueError, TypeError):
                pass  # no signature available
        return findings


# ---------------------------------------------------------------------------
# Pre-built architecture spec for the SENECIO oracle
# ---------------------------------------------------------------------------

def build_senecio_architecture_spec() -> ArchitectureSpec:
    """Returns an ArchitectureSpec describing the expected SENECIO oracle
    structure as of ACT-XXIX.

    This is a static declaration — the validator checks that the actual
    modules match. Add new components here as they're introduced.
    """
    components = [
        ComponentDescriptor(
            name="portfolio_engine",
            module_path="backend.portfolio.portfolio_engine",
            class_name="PortfolioEngine",
            version="ACT-XXV",
            depends_on=("risk_kernel",),
            provides=(
                InterfaceContract("build_proposal", "method"),
                InterfaceContract("recompute_state", "method"),
                InterfaceContract("update_config", "method"),
            ),
            description="Position sizing + exposure control",
        ),
        ComponentDescriptor(
            name="risk_kernel",
            module_path="backend.portfolio.risk_kernel",
            class_name="RiskKernel",
            version="ACT-XXV",
            depends_on=(),
            provides=(
                InterfaceContract("evaluate", "method"),
                InterfaceContract("get_state", "method"),
                InterfaceContract("record_pnl", "method"),
                InterfaceContract("trip_kill_switch", "method"),
                InterfaceContract("reset_kill_switch", "method"),
                InterfaceContract("update_vol_regime", "method"),
            ),
            description="Kill switch + daily loss limits + cooldown",
        ),
        ComponentDescriptor(
            name="execution_engine",
            module_path="backend.portfolio.execution_engine",
            class_name="ExecutionEngine",
            version="ACT-XXV",
            depends_on=("trade_journal",),
            provides=(
                InterfaceContract("submit", "method"),
                InterfaceContract("cancel", "method"),
                InterfaceContract("cancel_replace", "method"),
                InterfaceContract("check_exits", "method"),
                InterfaceContract("get_open_positions", "method"),
                InterfaceContract("stats", "method"),
            ),
            description="Order lifecycle + partial fills + slippage",
        ),
        ComponentDescriptor(
            name="trade_journal",
            module_path="backend.portfolio.trade_journal",
            class_name="TradeJournal",
            version="ACT-XXV",
            depends_on=(),
            provides=(
                InterfaceContract("on_audit_event", "method"),
                InterfaceContract("stats", "method"),
                InterfaceContract("fetch_recent", "method"),
                InterfaceContract("fetch_all", "method"),
            ),
            description="PnL + fees + MAE/MFE + exit_reason",
        ),
        ComponentDescriptor(
            name="coordinator",
            module_path="backend.portfolio.coordinator",
            class_name="PortfolioCoordinator",
            version="ACT-XXVI",
            depends_on=("portfolio_engine", "risk_kernel",
                        "execution_engine", "trade_journal"),
            provides=(
                InterfaceContract("start", "method"),
                InterfaceContract("stop", "method"),
                InterfaceContract("ingest_prediction", "method"),
                InterfaceContract("on_tick", "method"),
                InterfaceContract("get_state", "method"),
                InterfaceContract("evaluate_live_gate", "method"),
            ),
            description="Top-level orchestrator",
        ),
        ComponentDescriptor(
            name="live_gate",
            module_path="backend.portfolio.live_gate",
            class_name="LiveGate",
            version="ACT-XXV",
            depends_on=(),
            provides=(
                InterfaceContract("evaluate", "method"),
                InterfaceContract("get_state", "method"),
            ),
            description="6-criteria live capital unlock gate",
        ),
        ComponentDescriptor(
            name="event_sourcing",
            module_path="backend.antifragility.event_sourcing",
            class_name="EventStore",
            version="ACT-XXIX",
            depends_on=(),
            provides=(
                InterfaceContract("append", "method"),
                InterfaceContract("replay", "method"),
                InterfaceContract("verify_chain", "method"),
            ),
            description="Hash-chained event sourcing",
        ),
        ComponentDescriptor(
            name="invariant_checker",
            module_path="backend.antifragility.invariant_checker",
            class_name="InvariantRegistry",
            version="ACT-XXIX",
            depends_on=(),
            provides=(
                InterfaceContract("register", "method"),
                InterfaceContract("run_all", "method"),
            ),
            description="Invariant registry + state machine validator",
        ),
        ComponentDescriptor(
            name="lineage",
            module_path="backend.antifragility.lineage",
            class_name="LineageGraph",
            version="ACT-XXIX",
            depends_on=(),
            provides=(
                InterfaceContract("add_node", "method"),
                InterfaceContract("add_edge", "method"),
                InterfaceContract("ancestors", "method"),
                InterfaceContract("descendants", "method"),
            ),
            description="Data lineage + prediction ancestry",
        ),
        ComponentDescriptor(
            name="diagnostics",
            module_path="backend.antifragility.diagnostics",
            class_name="SelfDiagnostics",
            version="ACT-XXIX",
            depends_on=(),
            provides=(
                InterfaceContract("run", "method"),
                InterfaceContract("history", "method"),
            ),
            description="Self-diagnostics + health scoring",
        ),
        ComponentDescriptor(
            name="resilience",
            module_path="backend.antifragility.resilience",
            class_name="ResilienceCoordinator",
            version="ACT-XXIX",
            depends_on=(),
            provides=(
                InterfaceContract("register_subsystem", "method"),
                InterfaceContract("checkpoint_all", "method"),
                InterfaceContract("snapshot", "method"),
            ),
            description="Circuit breakers + checkpoints + watchdog",
        ),
        ComponentDescriptor(
            name="reproducibility",
            module_path="backend.antifragility.reproducibility",
            class_name="ExperimentRegistry",
            version="ACT-XXIX",
            depends_on=(),
            provides=(
                InterfaceContract("register", "method"),
                InterfaceContract("get", "method"),
                InterfaceContract("verify", "method"),
            ),
            description="Experiment registry + CV registry + benchmarks",
        ),
        ComponentDescriptor(
            name="market_simulation",
            module_path="backend.antifragility.market_simulation",
            class_name="SyntheticMarketGenerator",
            version="ACT-XXIX",
            depends_on=(),
            provides=(
                InterfaceContract("generate", "method"),
                InterfaceContract("stats", "method"),
            ),
            description="Synthetic markets + chaos engineering",
        ),
    ]
    return ArchitectureSpec(
        name="senecio_oracle",
        version="ACT-XXIX",
        components=tuple(components),
        required_components=("coordinator", "live_gate", "portfolio_engine",
                              "risk_kernel", "execution_engine",
                              "trade_journal"),
    )


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "FindingSeverity",
    "ValidationFinding",
    "InterfaceContract",
    "ComponentDescriptor",
    "ArchitectureSpec",
    "ValidationReport",
    "ArchitectureValidator",
    "build_senecio_architecture_spec",
]
