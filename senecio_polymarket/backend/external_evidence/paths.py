from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PERSISTENT_RESULTS_DIR = Path("/app/polymarket/results")
LEGACY_RESULTS_DIR = Path("data")


def _results_root() -> Path:
    override = (os.environ.get("SENEX_RESULTS_DIR") or "").strip()
    if override:
        return Path(override)
    if PERSISTENT_RESULTS_DIR.is_dir():
        return PERSISTENT_RESULTS_DIR
    return LEGACY_RESULTS_DIR


@dataclass(frozen=True)
class ExternalEvidencePaths:
    root: Path
    journal: Path
    blobs: Path

    @classmethod
    def default(cls) -> "ExternalEvidencePaths":
        override = (os.environ.get("SENEX_EXTERNAL_EVIDENCE_DIR") or "").strip()
        root = Path(override) if override else _results_root() / "external_evidence"
        return cls.from_root(root)

    @classmethod
    def from_root(cls, root: str | Path) -> "ExternalEvidencePaths":
        target = Path(root)
        return cls(
            root=target,
            journal=target / "events.jsonl",
            blobs=target / "blobs",
        )

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.blobs.mkdir(parents=True, exist_ok=True)
