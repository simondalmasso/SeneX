from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
MAIN_REAL = ROOT / "senecio_polymarket" / "backend" / "main_real.py"
LAUNCHER = ROOT / "senecio_polymarket" / "start_single_authority.sh"


def _main_real_tree() -> ast.Module:
    return ast.parse(MAIN_REAL.read_text(encoding="utf-8"))


def test_public_runtime_does_not_eagerly_import_legacy_main() -> None:
    tree = _main_real_tree()
    eager_main_imports = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level == 1:
            if node.module == "main" or any(alias.name == "main" for alias in node.names):
                eager_main_imports.append(node.lineno)
    assert eager_main_imports == [], (
        "main_real imports the legacy monolith during module import at "
        f"lines {eager_main_imports}; the public process must remain lightweight"
    )


def test_public_lifespan_refreshes_read_only_authority_without_starting_writer() -> None:
    tree = _main_real_tree()
    lifespan = next(
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "real_lifespan"
    )
    calls = [
        node.func.attr
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "oracle_runner"
    ]
    assert not ({"start", "initialize_portfolio_control"} & set(calls)), (
        "public monitor startup must not run the oracle writer or initialize PAPER control"
    )
    called_functions = {
        node.func.id if isinstance(node.func, ast.Name) else node.func.attr
        for node in ast.walk(lifespan)
        if isinstance(node, ast.Call) and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    assert {"_snapshot", "create_task"} <= called_functions, (
        "public monitor must bootstrap a read-only authority snapshot and schedule refresh"
    )


def test_public_launcher_never_starts_database_reconciler() -> None:
    launcher = LAUNCHER.read_text(encoding="utf-8")
    assert "backend.settlement_reconciler" not in launcher, (
        "the public monitor must not start the Supabase repair/reconciliation writer"
    )
    assert "SENEX_T0_REPLICATION_TOKEN" in launcher
    assert "SENEX_GPTRADER_INGEST_TOKEN" in launcher
    assert "exec uvicorn backend.main_real:app" in launcher


def test_importing_public_runtime_does_not_load_legacy_module() -> None:
    code = (
        "import os, sys; "
        "os.environ.pop('SENEX_ENABLE_SYNTHETIC_DEMO', None); "
        "import senecio_polymarket.backend.main_real; "
        "assert 'senecio_polymarket.backend.main' not in sys.modules"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_public_lifespan_does_not_start_writers_or_create_authority_seal(
    tmp_path: Path,
) -> None:
    env = patch.dict(
        os.environ,
        {
            "SENEX_AUTHORITY_SEAL_DIR": str(tmp_path),
            "SENEX_ENABLE_SETTLEMENT_RECONCILER": "0",
            "SENEX_ENABLE_SYNTHETIC_DEMO": "",
        },
        clear=False,
    )
    with env:
        from senecio_polymarket.backend import main_real, oracle_runner

        coordinator_before = oracle_runner.get_existing_portfolio_coordinator()
        with (
            patch.object(main_real._poly, "start", new=AsyncMock()),
            patch.object(main_real._poly, "stop", new=AsyncMock()),
            patch.object(main_real._boros, "start", new=AsyncMock()),
            patch.object(main_real._boros, "stop", new=AsyncMock()),
            patch.object(main_real._kalshi, "start", new=AsyncMock()),
            patch.object(main_real._kalshi, "stop", new=AsyncMock()),
            patch.object(main_real, "_snapshot", new=AsyncMock(return_value=None)) as snapshot,
            patch.object(main_real, "_authority_refresh_loop", new=AsyncMock()) as refresh_loop,
            patch.object(
                oracle_runner,
                "start",
                side_effect=AssertionError("startup must not start the oracle writer"),
            ),
            patch.object(
                oracle_runner,
                "initialize_portfolio_control",
                side_effect=AssertionError("startup must not bootstrap PAPER control"),
            ),
        ):
            async def exercise_lifespan() -> None:
                async with main_real.real_lifespan(main_real.app):
                    assert oracle_runner.get_existing_portfolio_coordinator() is coordinator_before

            asyncio.run(exercise_lifespan())
            snapshot.assert_awaited_once_with("BTCUSDT", force=True)
            refresh_loop.assert_called_once_with("BTCUSDT")

    assert list(tmp_path.iterdir()) == [], (
        "passive public startup created or changed something in authority_seals/"
    )