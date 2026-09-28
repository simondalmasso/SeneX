from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PERSISTENT_RESULTS_DIR = Path("/app/polymarket/results")
LEGACY_RESULTS_DIR = Path("data")


def _results_root() -> Path:
    override = os.environ.get("SENEX_RESULTS_DIR")
    if override:
        return Path(override)
    if PERSISTENT_RESULTS_DIR.is_dir():
        return PERSISTENT_RESULTS_DIR
    return LEGACY_RESULTS_DIR


@dataclass(frozen=True)
class GPTraderPaths:
    root: Path
    sealed_packets: Path
    decisions: Path
    decisions_index: Path
    trades: Path
    runs: Path
    cursor: Path
    packet_seq: Path
    verdict: Path
    mcp_audit: Path

    @classmethod
    def from_root(cls, root: str | Path | None = None) -> "GPTraderPaths":
        target = Path(root) if root is not None else _results_root() / "gptrader"
        return cls(
            root=target,
            sealed_packets=target / "sealed_packets.jsonl",
            decisions=target / "decisions.jsonl",
            decisions_index=target / "decisions.idx",
            trades=target / "trades.jsonl",
            runs=target / "runs.jsonl",
            cursor=target / "cursor.json",
            packet_seq=target / "packet_seq",
            verdict=target / "verdict.json",
            mcp_audit=target / "mcp_audit.jsonl",
        )

    def ensure_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
