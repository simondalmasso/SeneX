import pytest

from senecio_polymarket.backend.research.observability import MetricsRegistry


def test_time_call_unknown_metric_does_not_suppress_body_exception():
    registry = MetricsRegistry()

    with pytest.raises(RuntimeError, match="boom"):
        with registry.time_call("metric_that_is_not_registered"):
            raise RuntimeError("boom")
