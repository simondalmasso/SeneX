from senecio_polymarket.backend.oracle_runner import _seal_error_summary


def test_public_seal_error_summary_is_bounded():
    error = RuntimeError("x" * 1000)
    summary = _seal_error_summary(error)
    assert summary.startswith("RuntimeError: ")
    assert len(summary) <= 240


def test_durable_writer_timestamp_uses_persistence_wall_clock():
    from datetime import datetime, timezone

    from senecio_polymarket.backend import oracle_runner

    previous = oracle_runner._state["last_authority_persisted_ts"]
    before = datetime.now(timezone.utc)
    try:
        reported = oracle_runner._mark_authority_persisted()
        after = datetime.now(timezone.utc)
        persisted = datetime.fromisoformat(reported)
        assert before <= persisted <= after
        assert oracle_runner._state["last_authority_persisted_ts"] == reported
    finally:
        oracle_runner._state["last_authority_persisted_ts"] = previous
