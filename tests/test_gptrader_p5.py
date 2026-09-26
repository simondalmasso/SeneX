from __future__ import annotations

from pathlib import Path

from senecio_polymarket.backend.gptrader.task_protocol import (
    CALL_BUDGET_PER_RUN,
    DECISION_TOOL_ALLOWLIST,
    MAX_BATCHES_PER_RUN,
    TaskGate,
    task_gate,
)


def test_decision_task_tool_allowlist_is_exact() -> None:
    assert DECISION_TOOL_ALLOWLIST == (
        "get_gptrader_health",
        "get_prediction_batch",
        "get_gptrader_state",
        "submit_paper_decisions",
    )


def test_wait_mcp_and_safety_block_fail_closed() -> None:
    assert task_gate(None) == TaskGate.WAIT_MCP
    assert task_gate({"ready": False, "paper_only": True, "live": False}) == TaskGate.WAIT_MCP
    assert task_gate({"ready": True, "paper_only": False, "live": False}) == TaskGate.SAFETY_BLOCK
    assert task_gate({"ready": True, "paper_only": True, "live": True}) == TaskGate.SAFETY_BLOCK
    assert task_gate(
        {
            "ready": True,
            "paper_only": True,
            "simulation_only": True,
            "live": False,
        }
    ) == TaskGate.READY


def test_task_is_one_batch_and_three_calls_maximum() -> None:
    assert MAX_BATCHES_PER_RUN == 1
    assert CALL_BUDGET_PER_RUN == {
        "get_gptrader_health": 1,
        "get_prediction_batch": 1,
        "submit_paper_decisions": 1,
    }


def test_runbook_is_decision_mcp_only_and_no_fallback() -> None:
    runbook = (
        Path(__file__).resolve().parents[1]
        / "research"
        / "gptrader"
        / "order086"
        / "GPTRADER_HOURLY_PAPER.md"
    ).read_text(encoding="utf-8")
    assert "GPTrader Hourly PAPER" in runbook
    assert "Decision MCP only" in runbook
    assert "MAX_BATCHES_PER_RUN=1" in runbook
    assert "WAIT_MCP" in runbook
    assert "SAFETY_BLOCK" in runbook
    assert "NO_FALLBACK_DATA=true" in runbook
    assert "SCIENTIFIC_SPINE_REQUIRES_CHATGPT=false" in runbook
    assert "PERSIST_BEFORE_SETTLEMENT=true" in runbook


def test_runbook_does_not_authorize_live_or_real_orders() -> None:
    runbook = (
        Path(__file__).resolve().parents[1]
        / "research"
        / "gptrader"
        / "order086"
        / "GPTRADER_HOURLY_PAPER.md"
    ).read_text(encoding="utf-8")
    assert "PAPER_ONLY=true" in runbook
    assert "LIVE=false" in runbook
    assert "REAL_ORDERS=0" in runbook
    assert "CAPITAL=0" in runbook
