"""SENECIO ORACLE — Watchdogs package (ACT FINAL_AUDIT A4)."""
from .coordinator import (
    run_all_watchdogs,
    latest_alerts,
    WATCHDOGS,
    ALERTS_FILE,
)

__all__ = [
    "run_all_watchdogs",
    "latest_alerts",
    "WATCHDOGS",
    "ALERTS_FILE",
]
