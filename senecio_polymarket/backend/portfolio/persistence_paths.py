from __future__ import annotations

import os
from pathlib import Path

PERSISTENT_RESULTS_DIR = Path("/app/polymarket/results")


def results_dir() -> Path | None:
    override = os.environ.get("SENEX_RESULTS_DIR")
    if override:
        return Path(override)
    if PERSISTENT_RESULTS_DIR.is_dir():
        return PERSISTENT_RESULTS_DIR
    return None


def resolve_path(filename: str, legacy: str, explicit: str | None = None, env_key: str | None = None) -> str:
    if explicit:
        return explicit
    if env_key and os.environ.get(env_key):
        return os.environ[env_key]
    root = results_dir()
    return str(root / filename) if root else legacy
