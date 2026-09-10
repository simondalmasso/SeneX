"""
SENECIO ORACLE — ACT XXVII Priority 6: Observability
=====================================================

Prometheus-style metrics + timing instrumentation for every subsystem of
the oracle pipeline. Exposes a single `/metrics` endpoint (handled in
main.py) that the Prometheus scraper can poll.

Metric families:
  - Counters     : monotonic counts (predictions_total, trades_total, ...)
  - Histograms   : latency distributions (prediction_latency_seconds,
                   execution_latency_seconds, risk_evaluation_latency_seconds)
  - Gauges       : point-in-time values (open_positions, equity_usd,
                   memory_usage_bytes, drift_alerts_active)
  - Summaries    : for high-cardinality timing if needed

The observability layer is a SINGLETON — there is one global `MetricsRegistry`
instance shared across the FastAPI app. This keeps metric cardinality bounded
and lets any module instrument itself without plumbing.

This module is STRICT_ADDITIVE — does NOT touch prediction_model /
feature_engineering / signal_generation / verifier. Other modules
opt-in to instrumentation by calling `time_call()` or `observe()`.
"""
from __future__ import annotations

import gc
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger("senecio.research.observability")


# ---------------------------------------------------------------------------
# PrometheusClient (always available — installed in ACT-XXVII requirements)
# ---------------------------------------------------------------------------

try:
    from prometheus_client import (
        CollectorRegistry, Counter, Histogram, Gauge, Summary,
        generate_latest, CONTENT_TYPE_LATEST,
    )
    _HAS_PROM = True
except ImportError:  # pragma: no cover — prometheus_client is required
    _HAS_PROM = False
    CollectorRegistry = None  # type: ignore


# ---------------------------------------------------------------------------
# Metric specs (defined once, reused across the registry)
# ---------------------------------------------------------------------------


@dataclass
class MetricSpec:
    name: str
    help: str
    kind: str   # "counter" | "histogram" | "gauge" | "summary"
    labels: tuple[str, ...] = field(default_factory=tuple)
    buckets: Optional[list[float]] = None    # for histograms


DEFAULT_METRIC_SPECS: list[MetricSpec] = [
    # --- Counters ---
    MetricSpec(
        "senecio_predictions_total", "Total oracle predictions generated",
        "counter", ("direction", "outcome_window"),
    ),
    MetricSpec(
        "senecio_predictions_verified_total",
        "Predictions whose outcome was verified by the oracle verifier",
        "counter", ("outcome", "outcome_window"),
    ),
    MetricSpec(
        "senecio_trades_total", "Total paper trades executed by ExecutionEngine",
        "counter", ("direction", "exit_reason"),
    ),
    MetricSpec(
        "senecio_risk_decisions_total",
        "Total RiskKernel decisions (approved/rejected)",
        "counter", ("decision",),
    ),
    MetricSpec(
        "senecio_drift_warnings_total",
        "Total drift warnings emitted by DriftMonitor",
        "counter", ("detector", "severity"),
    ),
    MetricSpec(
        "senecio_research_runs_total",
        "Total research-module invocations",
        "counter", ("module",),
    ),
    MetricSpec(
        "senecio_calibration_fits_total",
        "Total probability-calibration fits",
        "counter", ("method",),
    ),
    MetricSpec(
        "senecio_explainer_fits_total",
        "Total explainer fits",
        "counter", ("model_type", "explainer_kind"),
    ),
    MetricSpec(
        "senecio_kills_total", "Total kill-switch trips", "counter",
    ),

    # --- Histograms (latency) ---
    MetricSpec(
        "senecio_prediction_latency_seconds",
        "Wall-clock time for one oracle prediction cycle",
        "histogram",
        buckets=[0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0],
    ),
    MetricSpec(
        "senecio_execution_latency_seconds",
        "Wall-clock time for ExecutionEngine.submit()",
        "histogram",
        buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
    ),
    MetricSpec(
        "senecio_risk_evaluation_latency_seconds",
        "Wall-clock time for RiskKernel.evaluate()",
        "histogram",
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
    ),
    MetricSpec(
        "senecio_portfolio_engine_latency_seconds",
        "Wall-clock time for PortfolioEngine.build_proposal()",
        "histogram",
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
    ),
    MetricSpec(
        "senecio_microstructure_latency_seconds",
        "Wall-clock time for MicrostructureIntelligence.evaluate()",
        "histogram",
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
    ),
    MetricSpec(
        "senecio_meta_labeler_latency_seconds",
        "Wall-clock time for MetaLabeler.evaluate()",
        "histogram",
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1],
    ),
    MetricSpec(
        "senecio_research_module_latency_seconds",
        "Wall-clock time for a research module invocation",
        "histogram", ("module",),
        buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
    ),

    # --- Gauges ---
    MetricSpec(
        "senecio_open_positions",
        "Current number of open paper positions",
        "gauge",
    ),
    MetricSpec(
        "senecio_equity_usd",
        "Current paper-mode equity (USD)",
        "gauge",
    ),
    MetricSpec(
        "senecio_cash_usd", "Current paper-mode cash (USD)", "gauge",
    ),
    MetricSpec(
        "senecio_portfolio_heat_pct",
        "Current portfolio heat as % of equity at risk",
        "gauge",
    ),
    MetricSpec(
        "senecio_kill_switch_active",
        "1 if kill switch is currently tripped, 0 otherwise",
        "gauge",
    ),
    MetricSpec(
        "senecio_live_gate_unlocked",
        "1 if LIVE gate is unlocked, 0 otherwise (always 0 in PAPER mode)",
        "gauge",
    ),
    MetricSpec(
        "senecio_drift_alerts_active",
        "Number of currently-active drift alerts (across all detectors)",
        "gauge",
    ),
    MetricSpec(
        "senecio_memory_usage_bytes",
        "Process RSS memory in bytes (updated periodically)",
        "gauge",
    ),
    MetricSpec(
        "senecio_process_uptime_seconds",
        "Process uptime in seconds",
        "gauge",
    ),
    MetricSpec(
        "senecio_last_calibration_ece",
        "Most recent Expected Calibration Error from the calibration module",
        "gauge", ("method",),
    ),
    MetricSpec(
        "senecio_last_ic",
        "Most recent Information Coefficient from research_metrics",
        "gauge",
    ),
    MetricSpec(
        "senecio_rolling_sharpe",
        "Latest rolling Sharpe from research_metrics",
        "gauge",
    ),
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class MetricsRegistry:
    """Singleton Prometheus metrics registry for the whole app.

    Usage:
        reg = MetricsRegistry.instance()
        with reg.time_call("senecio_prediction_latency_seconds"):
            ...do prediction...
        reg.observe("senecio_predictions_total", 1,
                    labels={"direction": "LONG", "outcome_window": "1h"})
        reg.set_gauge("senecio_open_positions", 3)

    The `.expose()` method returns Prometheus exposition format bytes.
    """

    _instance: Optional["MetricsRegistry"] = None
    _lock = threading.Lock()

    def __init__(self, registry: Optional[Any] = None):
        if not _HAS_PROM:
            log.warning(
                "prometheus_client not installed — observability metrics "
                "will be no-ops. Add 'prometheus_client' to requirements."
            )
            self._registry = None
            self._metrics: dict[str, Any] = {}
            self._start_time = time.time()
            self._counters: dict[str, float] = {}
            self._gauges: dict[str, float] = {}
            self._histograms_samples: dict[str, list[float]] = {}
            return
        self._registry = registry or CollectorRegistry()
        self._metrics: dict[str, Any] = {}
        self._start_time = time.time()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms_samples: dict[str, list[float]] = {}
        # Register all default specs
        for spec in DEFAULT_METRIC_SPECS:
            self._register(spec)

    @classmethod
    def instance(cls) -> "MetricsRegistry":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _register(self, spec: MetricSpec) -> None:
        if not _HAS_PROM:
            return
        try:
            if spec.kind == "counter":
                self._metrics[spec.name] = Counter(
                    spec.name, spec.help, list(spec.labels),
                    registry=self._registry,
                )
            elif spec.kind == "histogram":
                self._metrics[spec.name] = Histogram(
                    spec.name, spec.help, list(spec.labels),
                    buckets=spec.buckets, registry=self._registry,
                )
            elif spec.kind == "gauge":
                self._metrics[spec.name] = Gauge(
                    spec.name, spec.help, list(spec.labels),
                    registry=self._registry,
                )
            elif spec.kind == "summary":
                self._metrics[spec.name] = Summary(
                    spec.name, spec.help, list(spec.labels),
                    registry=self._registry,
                )
        except Exception as e:
            log.warning("failed to register metric %s: %s", spec.name, e)

    # -------- public API --------

    def observe(
        self, metric_name: str, value: float = 1.0,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Increment a counter or observe a histogram value."""
        if _HAS_PROM:
            m = self._metrics.get(metric_name)
            if m is None:
                log.debug("metric not registered: %s", metric_name)
                return
            try:
                if isinstance(m, (Counter, Histogram, Summary)):
                    if labels:
                        m.labels(**labels).inc(value if isinstance(m, Counter) else 0)
                        if isinstance(m, Histogram):
                            m.labels(**labels).observe(value)
                    else:
                        if isinstance(m, Counter):
                            m.inc(value)
                        elif isinstance(m, Histogram):
                            m.observe(value)
            except Exception as e:
                log.debug("metric observe failed (%s): %s", metric_name, e)
        else:
            # No-op fallback — track counts in-memory so we can still produce
            # a basic JSON snapshot for /api/observability
            if metric_name not in self._counters:
                self._counters[metric_name] = 0.0
                self._histograms_samples[metric_name] = []
            self._counters[metric_name] += value
            self._histograms_samples.setdefault(metric_name, []).append(value)

    def set_gauge(
        self, metric_name: str, value: float,
        labels: Optional[dict[str, str]] = None,
    ) -> None:
        """Set a gauge to a specific value."""
        if _HAS_PROM:
            m = self._metrics.get(metric_name)
            if m is None:
                return
            try:
                if isinstance(m, Gauge):
                    if labels:
                        m.labels(**labels).set(value)
                    else:
                        m.set(value)
            except Exception as e:
                log.debug("gauge set failed (%s): %s", metric_name, e)
        else:
            self._gauges[metric_name] = float(value)

    @contextmanager
    def time_call(
        self, metric_name: str,
        labels: Optional[dict[str, str]] = None,
    ) -> Iterator[None]:
        """Context manager that records wall-clock time into a histogram."""
        start = time.perf_counter()
        try:
            yield
        finally:
            elapsed = time.perf_counter() - start
            if _HAS_PROM:
                m = self._metrics.get(metric_name)
                if m is None:
                    return
                try:
                    if isinstance(m, (Histogram, Summary)):
                        if labels:
                            m.labels(**labels).observe(elapsed)
                        else:
                            m.observe(elapsed)
                except Exception as e:
                    log.debug("time_call observe failed (%s): %s", metric_name, e)
            else:
                self._histograms_samples.setdefault(metric_name, []).append(elapsed)

    def expose(self) -> tuple[bytes, str]:
        """Return (bytes, content_type) for the /metrics endpoint."""
        if _HAS_PROM:
            return generate_latest(self._registry), CONTENT_TYPE_LATEST
        # Fallback: emit a text/plain snapshot
        lines = ["# senecio fallback metrics (prometheus_client not installed)"]
        for name, val in sorted(self._counters.items()):
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name} {val}")
        for name, val in sorted(self._gauges.items()):
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {val}")
        for name, samples in sorted(self._histograms_samples.items()):
            if not samples:
                continue
            n = len(samples)
            mean = sum(samples) / n
            lines.append(f"# TYPE {name} histogram")
            lines.append(f'{name}_count {n}')
            lines.append(f'{name}_sum {sum(samples)}')
            lines.append(f'# mean {mean}')
        body = "\n".join(lines) + "\n"
        return body.encode("utf-8"), "text/plain; version=0.0.4"

    def update_runtime_metrics(self) -> None:
        """Refresh gauges that come from process state (memory, uptime)."""
        try:
            import resource
            rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # On Linux ru_maxrss is in KB; on macOS it's in bytes. We assume Linux
            # (Northflank runs Linux). Convert KB → bytes.
            rss_bytes = rss_kb * 1024
            self.set_gauge("senecio_memory_usage_bytes", float(rss_bytes))
        except Exception:
            pass
        try:
            uptime = time.time() - self._start_time
            self.set_gauge("senecio_process_uptime_seconds", float(uptime))
        except Exception:
            pass

    def stats(self) -> dict[str, Any]:
        """Lightweight JSON snapshot of current metric values (for /api/observability)."""
        self.update_runtime_metrics()
        out: dict[str, Any] = {
            "prometheus_available": _HAS_PROM,
            "started_at": float(self._start_time),
            "uptime_seconds": time.time() - self._start_time,
            "counters": dict(self._counters) if not _HAS_PROM else None,
            "gauges": dict(self._gauges) if not _HAS_PROM else None,
        }
        if _HAS_PROM:
            try:
                # Walk the registry and produce a small summary
                from prometheus_client.parser import text_string_to_metric_families
                body, _ = self.expose()
                families = list(text_string_to_metric_families(body.decode("utf-8")))
                summaries: list[dict[str, Any]] = []
                for fam in families:
                    for sample in fam.samples:
                        summaries.append({
                            "name": sample.name,
                            "labels": dict(sample.labels),
                            "value": float(sample.value),
                        })
                out["prometheus_samples"] = summaries
            except Exception as e:
                out["prometheus_error"] = str(e)
        return out


# ---------------------------------------------------------------------------
# Convenience module-level accessor
# ---------------------------------------------------------------------------


def get_registry() -> MetricsRegistry:
    """Return the singleton MetricsRegistry."""
    return MetricsRegistry.instance()


@contextmanager
def timed(metric_name: str, labels: Optional[dict[str, str]] = None) -> Iterator[None]:
    """Module-level shortcut for `MetricsRegistry.instance().time_call(...)`."""
    reg = get_registry()
    with reg.time_call(metric_name, labels=labels):
        yield


__all__ = [
    "MetricSpec",
    "DEFAULT_METRIC_SPECS",
    "MetricsRegistry",
    "get_registry",
    "timed",
]
