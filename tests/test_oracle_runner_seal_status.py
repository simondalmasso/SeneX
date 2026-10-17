from senecio_polymarket.backend.oracle_runner import _seal_error_summary


def test_public_seal_error_summary_is_bounded():
    error = RuntimeError("x" * 1000)
    summary = _seal_error_summary(error)
    assert summary.startswith("RuntimeError: ")
    assert len(summary) <= 240
