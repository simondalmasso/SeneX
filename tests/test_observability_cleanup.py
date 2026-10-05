import pytest

from senecio_polymarket.backend.research.observability import (
    DEFAULT_METRIC_SPECS,
    MetricsRegistry,
)


def test_time_call_unknown_metric_does_not_suppress_body_exception():
    registry = MetricsRegistry()

    with pytest.raises(RuntimeError, match="boom"):
        with registry.time_call("metric_that_is_not_registered"):
            raise RuntimeError("boom")


def test_monte_carlo_ruin_probability_has_dedicated_gauge():
    names = {spec.name for spec in DEFAULT_METRIC_SPECS}
    assert "senecio_monte_carlo_ruin_probability" in names
