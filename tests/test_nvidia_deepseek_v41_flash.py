from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = (
    ROOT
    / "research"
    / "gptrader"
    / "providers"
    / "nvidia_deepseek_v41_flash.py"
)


def _load():
    spec = importlib.util.spec_from_file_location(
        "nvidia_deepseek_v41_flash", MODULE_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_provider_profile_is_exact_and_secret_stays_only_in_memory():
    m = _load()
    secret = "nvapi-" + ("x" * 48)
    overrides = m.provider_overrides({"NVIDIA_API_KEY": secret})

    assert overrides == {
        "SENEX_DECISION_PROVIDER_BASE_URL": "https://integrate.api.nvidia.com/v1",
        "SENEX_DECISION_PROVIDER_MODEL": "deepseek-ai/deepseek-v4.1-flash",
        "SENEX_DECISION_PROVIDER_API_KEY": secret,
    }
    assert secret not in MODULE_PATH.read_text(encoding="utf-8")


def test_injected_offline_probe_uses_existing_openai_contract_only():
    m = _load()
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["headers"] = dict(headers)
        captured["payload"] = payload
        captured["timeout"] = timeout
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "decisions": [
                                    {
                                        "packet_id": m.PROBE_PACKET["packet_id"],
                                        "action": "ABSTAIN",
                                        "reason_codes": ["PROVIDER_PROBE"],
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    result = m.run_probe(api_key="k" * 48, http_post=fake_post)

    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["payload"]["model"] == "deepseek-ai/deepseek-v4.1-flash"
    assert "temperature" not in captured["payload"]
    wire = json.dumps(captured["payload"], sort_keys=True)
    assert "synthetic_probe" in wire
    assert "settlement" not in wire
    assert "outcome" not in wire
    assert result["status"] == "PASS"
    assert result["network_used"] is False
    assert result["mcp_contacted"] is False
    assert result["decision_submitted"] is False


def test_probe_rejects_extra_model_fields_via_existing_senex_schema():
    m = _load()

    def fake_post(url, headers, payload, timeout):
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "decisions": [
                                    {
                                        "packet_id": m.PROBE_PACKET["packet_id"],
                                        "action": "TAKE",
                                        "direction": "SHORT",
                                        "reason_codes": ["BAD"],
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    with pytest.raises(Exception, match="forbidden|unknown"):
        m.run_probe(api_key="k" * 48, http_post=fake_post)


def test_run_probe_cannot_use_real_network_without_injected_transport():
    m = _load()
    with pytest.raises(RuntimeError, match="network disabled"):
        m.run_probe(api_key="k" * 48)


def test_cli_without_key_is_blocked_before_network(monkeypatch, capsys):
    m = _load()
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    code = m.main([])
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["status"] == "BLOCKED_NO_NVIDIA_API_KEY"
    assert payload["network_used"] is False
    assert payload["mcp_contacted"] is False
    assert payload["decision_submitted"] is False
    assert payload["zero_cost_owner_lock"] is True


def test_cli_with_key_still_cannot_enable_network(monkeypatch, capsys):
    m = _load()
    monkeypatch.setenv("NVIDIA_API_KEY", "k" * 48)

    code = m.main([])
    payload = json.loads(capsys.readouterr().out)

    assert code == 2
    assert payload["status"] == "BLOCKED_EXTERNAL_PROVIDER_NETWORK_DISABLED"
    assert payload["network_used"] is False
    assert payload["zero_cost_owner_lock"] is True


def test_provider_overrides_require_key():
    m = _load()
    with pytest.raises(RuntimeError, match="NVIDIA_API_KEY"):
        m.provider_overrides({})
