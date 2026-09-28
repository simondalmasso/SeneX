from __future__ import annotations

from enum import Enum
from typing import Any

DECISION_TOOL_ALLOWLIST = (
    "get_gptrader_health",
    "get_prediction_batch",
    "get_gptrader_state",
    "submit_paper_decisions",
)

MAX_BATCHES_PER_RUN = 1

CALL_BUDGET_PER_RUN = {
    "get_gptrader_health": 1,
    "get_prediction_batch": 1,
    "submit_paper_decisions": 1,
}


class TaskGate(str, Enum):
    READY = "READY"
    WAIT_MCP = "WAIT_MCP"
    SAFETY_BLOCK = "SAFETY_BLOCK"


def task_gate(health: dict[str, Any] | None) -> TaskGate:
    """Fail closed before any decision batch is requested."""

    if not isinstance(health, dict):
        return TaskGate.WAIT_MCP
    if health.get("ready") is not True:
        return TaskGate.WAIT_MCP

    if health.get("paper_only") is not True:
        return TaskGate.SAFETY_BLOCK
    if health.get("simulation_only") is not True:
        return TaskGate.SAFETY_BLOCK
    if health.get("live") is not False:
        return TaskGate.SAFETY_BLOCK

    return TaskGate.READY
