from pathlib import Path

from senecio_polymarket.backend.gptrader.gptrader_view import gptrader_public_state

ROOT = Path(__file__).resolve().parents[1]
START = (ROOT / "senecio_polymarket" / "start_single_authority.sh").read_text(encoding="utf-8")
DOCKER = (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_launcher_has_opt_in_durable_mcp_sidecar_contract():
    assert "SENEX_GPTRADER_MCP_ENABLED" in START
    assert "SENEX_GPTRADER_MCP_RESULTS_DIR" in START
    assert "/app/polymarket/results/gptrader-mcp-runtime" in START
    assert "backend.gptrader.mcp_http:create_app_from_env" in START
    assert "--factory" in START
    assert '--port "$MCP_PORT"' in START
    assert "--workers 1" in START
    assert "MCP_PID=$!" in START


def test_launcher_scopes_sidecar_results_without_repointing_producer():
    assert 'SENEX_RESULTS_DIR="$MCP_RESULTS_DIR"' in START
    assert 'export SENEX_GPTRADER_VIEW_ROOT="$MCP_RESULTS_DIR/gptrader"' in START
    assert 'export SENEX_RESULTS_DIR="$MCP_RESULTS_DIR"' not in START


def test_launcher_fails_closed_on_mcp_process_exit_and_cleans_it_up():
    assert 'kill -0 "$MCP_PID"' in START
    assert "MCP sidecar exited" in START
    assert 'kill -TERM "${MCP_PID:-}"' in START
    assert 'wait "${MCP_PID:-}"' in START


def test_docker_exposes_mcp_port_and_healthchecks_it_when_enabled():
    assert "EXPOSE 8080 8787" in DOCKER
    assert "SENEX_GPTRADER_MCP_ENABLED" in DOCKER
    assert "HEALTHCHECK" in DOCKER and "\\n  CMD curl" in DOCKER
    assert "8787}/healthz" in DOCKER


def test_public_view_root_can_follow_consumer_without_changing_explicit_root(tmp_path, monkeypatch):
    producer = tmp_path / "producer"
    consumer = tmp_path / "consumer"
    producer.mkdir()
    consumer.mkdir()
    (producer / "decisions.jsonl").write_text(
        '{"policy_id":"p","packet_id":"producer","action":"ABSTAIN"}\n',
        encoding="utf-8",
    )
    (consumer / "decisions.jsonl").write_text(
        '{"policy_id":"p","packet_id":"consumer","action":"TAKE"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("SENEX_GPTRADER_VIEW_ROOT", str(consumer))
    env_view = gptrader_public_state()
    explicit_view = gptrader_public_state(producer)
    assert env_view["take_count"] == 1
    assert env_view["abstain_count"] == 0
    assert explicit_view["take_count"] == 0
    assert explicit_view["abstain_count"] == 1
