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
