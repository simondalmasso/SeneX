"""SENEX B8.1 candidate HARD PAPER LOCK — structural top-level interlock.

This module is the smallest top-level safety interlock that makes accidental
live-capital activation IMPOSSIBLE in this candidate:

  * trade_mode is PAPER;
  * orders are disabled;
  * live capital is locked;
  * wallet signatures / real orders / fund movements are structurally absent
    from every execution path (there is no broker adapter wired anywhere).

The lock is a MODULE CONSTANT. It deliberately does NOT consult:
  - environment variables;
  - configuration files;
  - runtime state;
  - the UI;
  - LiveGate results;
  - stale persisted state.

Therefore no env var, config value, API input, or gate evaluation can open
it. Removing the lock requires editing this source file and rebuilding the
artifact (which changes build_digest/source_tree and voids exact=true).

Execution abstractions (ExecutionEngine, ExecutionSimulator, LiveGate) are
PRESERVED: they keep simulating realistic PAPER execution. Only the live
unlock paths are structurally refused.
"""
from __future__ import annotations

# Structural constant for THIS candidate. Intentionally not configurable.
HARD_PAPER_LOCK = True

HARD_PAPER_LOCK_VERSION = "senex-hard-paper-lock-v1"


class HardPaperLockError(RuntimeError):
    """Raised when any code path attempts to activate live execution."""


def hard_paper_lock_active() -> bool:
    """True when the candidate-level paper lock is engaged (always, here)."""
    return HARD_PAPER_LOCK


def assert_paper_locked(context: str) -> None:
    """Refuse live activation attempts from any caller."""
    if hard_paper_lock_active():
        raise HardPaperLockError(
            f"HARD_PAPER_LOCK: live execution is structurally disabled in this "
            f"candidate; refusing: {context}"
        )


def safety_projection() -> dict[str, bool | str]:
    """Canonical safety block for health/readiness/dashboard surfaces."""
    return {
        "trade_mode": "PAPER",
        "orders_enabled": False,
        "live_capital_locked": True,
        "hard_paper_lock": hard_paper_lock_active(),
        "hard_paper_lock_version": HARD_PAPER_LOCK_VERSION,
    }
