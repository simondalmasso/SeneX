from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "senecio_polymarket" / "backend"
RUNTIME_FILES = [
    BACKEND / "main_real.py", BACKEND / "oracle_runner.py",
    ROOT / "senecio_polymarket" / "start_single_authority.sh", ROOT / "Dockerfile",
    ROOT / "senecio_polymarket" / "frontend" / "index.html",
    ROOT / "senecio_polymarket" / "frontend" / "app.js",
]


def test_decision_package_and_external_provider_implementation_are_removed():
    assert not (BACKEND / "gptrader").exists()
    assert not (ROOT / "research" / "gptrader" / "providers" / "nvidia_deepseek_v41_flash.py").exists()
    assert not (ROOT / "docs" / "NVIDIA_DEEPSEEK_V41_FLASH_PAPER.md").exists()


def test_runtime_has_no_decision_service_or_model_sidecar_reference():
    for path in RUNTIME_FILES:
        text = path.read_text(encoding="utf-8").lower()
        assert "backend.gptrader" not in text
        assert "api/gptrader/" not in text
        assert "gptrader-mcp" not in text
    launcher = (ROOT / "senecio_polymarket" / "start_single_authority.sh").read_text(encoding="utf-8")
    docker = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "MCP_ENABLED" not in launcher
    assert "MCP_PID" not in launcher
    assert "EXPOSE 8080 8787" not in docker
    assert "SENEX_GPTRADER_MCP_ENABLED" not in docker


def test_prediction_lineage_keeps_the_separate_t0_audit_dependency():
    oracle = (BACKEND / "oracle_runner.py").read_text(encoding="utf-8")
    assert "from .t0_audit.sealer import build_sealed_packet, seal_prediction_t0" in oracle
    assert "from .t0_audit.transport import replicate_pending_t0" in oracle
    assert "sealed packet identity diverged from receipt outbox" in oracle
