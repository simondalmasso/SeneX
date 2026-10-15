from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PERSISTENT_RESULTS_DIR = Path("/app/polymarket/results")
LEGACY_RESULTS_DIR = Path("data")
# Historical T0 receipts remain in the original directory for evidence continuity.
LEGACY_T0_RESULTS_SUBDIR = "gptrader"


def _results_root() -> Path:
    override = os.environ.get("SENEX_RESULTS_DIR")
    if override:
        return Path(override)
    if PERSISTENT_RESULTS_DIR.is_dir():
        return PERSISTENT_RESULTS_DIR
    return LEGACY_RESULTS_DIR


@dataclass(frozen=True)
class T0AuditPaths:
    root: Path
    sealed_packets: Path
    packet_seq: Path

    @classmethod
    def from_root(cls, root: str | Path | None = None) -> "T0AuditPaths":
        target = Path(root) if root is not None else _results_root() / LEGACY_T0_RESULTS_SUBDIR
        return cls(root=target, sealed_packets=target / "sealed_packets.jsonl", packet_seq=target / "packet_seq")

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
