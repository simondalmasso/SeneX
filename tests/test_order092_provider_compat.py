from __future__ import annotations

import json

from senecio_polymarket.backend.gptrader.external_client import (
    OpenAICompatibleDecisionAdapter,
)


def test_order092_provider_payload_omits_temperature_for_gpt5_compatibility():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "decisions": [
                                    {
                                        "packet_id": "pkt-1",
                                        "action": "ABSTAIN",
                                        "reason_codes": ["COMPAT"],
                                    }
                                ]
                            }
                        )
                    }
                }
            ]
        }

    adapter = OpenAICompatibleDecisionAdapter(
        base_url="https://api.openai.com/v1",
        model="gpt-5-mini",
        api_key="k" * 48,
        http_post=fake_post,
    )
    adapter.decide(
        [{"packet_id": "pkt-1", "prediction": "LONG", "confidence": 0.5}],
        run_id="compat",
    )

    assert "temperature" not in captured["payload"]


def test_nvidia_deepseek_v41_flash_profile_uses_openai_wire_and_bounded_output():
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
                                        "packet_id": "pkt-1",
                                        "action": "ABSTAIN",
                                        "reason_codes": ["NVIDIA_SHADOW"],
                                    }
                                ]
                            },
                            separators=(",", ":"),
                        )
                    }
                }
            ]
        }

    adapter = OpenAICompatibleDecisionAdapter(
        base_url="https://integrate.api.nvidia.com/v1",
        model="deepseek-ai/deepseek-v4.1-flash",
        api_key="nvapi-" + ("x" * 48),
        max_tokens=1024,
        http_post=fake_post,
    )
    output = adapter.decide(
        [{"packet_id": "pkt-1", "prediction": "LONG", "confidence": 0.61}],
        run_id="nvidia-shadow",
    )

    assert output["decisions"][0]["action"] == "ABSTAIN"
    assert captured["url"] == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert captured["payload"]["model"] == "deepseek-ai/deepseek-v4.1-flash"
    assert captured["payload"]["max_tokens"] == 1024
    assert "temperature" not in captured["payload"]
    wire = json.dumps(captured["payload"], sort_keys=True)
    assert "settlement" not in wire
    assert "outcome" not in wire
    assert "current_market" not in wire
    assert "nvidia-shadow" not in wire


def test_provider_max_tokens_env_is_optional_and_validated(monkeypatch):
    from senecio_polymarket.backend.gptrader.external_client import (
        create_external_client_from_env,
    )

    monkeypatch.setenv("SENEX_GPTRADER_MCP_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("SENEX_GPTRADER_MCP_TOKEN", "m" * 48)
    monkeypatch.setenv(
        "SENEX_DECISION_PROVIDER_BASE_URL",
        "https://integrate.api.nvidia.com/v1",
    )
    monkeypatch.setenv(
        "SENEX_DECISION_PROVIDER_MODEL",
        "deepseek-ai/deepseek-v4.1-flash",
    )
    monkeypatch.setenv("SENEX_DECISION_PROVIDER_API_KEY", "n" * 48)
    monkeypatch.setenv("SENEX_DECISION_PROVIDER_MAX_TOKENS", "1024")
    monkeypatch.setenv("SENEX_GPTRADER_BATCH_LIMIT", "4")

    client = create_external_client_from_env()
    assert client.batch_limit == 4
    assert client.adapter.max_tokens == 1024
    assert client.adapter.base_url == "https://integrate.api.nvidia.com/v1"
    assert client.adapter.model == "deepseek-ai/deepseek-v4.1-flash"

    monkeypatch.setenv("SENEX_DECISION_PROVIDER_MAX_TOKENS", "not-an-int")
    with pytest.raises(ValueError, match="must be an integer"):
        create_external_client_from_env()
