from __future__ import annotations

import json
import traceback

import pytest

from senecio_polymarket.backend.gptrader import external_client as external_client_module
from senecio_polymarket.backend.gptrader.external_client import (
    DecisionOutputError,
    ExternalDecisionClient,
    MCPJSONRPCClient,
    MCPUnavailable,
    OpenAICompatibleDecisionAdapter,
    normalize_decisions,
)


def _health(**overrides):
    value = {
        "ready": True,
        "paper_only": True,
        "simulation_only": True,
        "live": False,
        "schema_version": "gptrader.decision.v1",
    }
    value.update(overrides)
    return value


def _packets():
    return [
        {"packet_id": "pkt-1", "packet_seq": 1, "prediction": "LONG", "confidence": 0.61},
        {"packet_id": "pkt-2", "packet_seq": 2, "prediction": "SHORT", "confidence": 0.57},
    ]


def _abstain_output():
    return {
        "decisions": [
            {"packet_id": "pkt-1", "action": "ABSTAIN", "reason_codes": ["CANARY"]},
            {"packet_id": "pkt-2", "action": "ABSTAIN", "reason_codes": ["CANARY"]},
        ]
    }


class FakeMCP:
    def __init__(self, health=None):
        self.health = _health() if health is None else health
        self.health_calls = 0
        self.batch_calls = 0
        self.submit_calls = 0
        self.submitted = None

    def get_gptrader_health(self):
        self.health_calls += 1
        if isinstance(self.health, Exception):
            raise self.health
        return self.health

    def get_prediction_batch(self, cursor=None, limit=8):
        self.batch_calls += 1
        return {
            "cursor_in": "c0",
            "next_cursor": "c2",
            "has_more": False,
            "packets": _packets(),
        }

    def submit_paper_decisions(self, run_id, cursor, decisions):
        self.submit_calls += 1
        self.submitted = (run_id, cursor, decisions)
        return {"applied": len(decisions), "duplicate": False, "cursor": "c2"}


class StaticAdapter:
    def __init__(self, output):
        self.output = output
        self.calls = 0

    def decide(self, packets, *, run_id):
        self.calls += 1
        return self.output


def test_mcp_unavailable_waits_without_requesting_batch_or_submitting():
    mcp = FakeMCP(MCPUnavailable("unavailable"))
    client = ExternalDecisionClient(mcp=mcp, adapter=StaticAdapter(_abstain_output()))

    result = client.run_once(run_id="r1")

    assert result["gate"] == "WAIT_MCP"
    assert result["submitted"] is False
    assert mcp.batch_calls == 0
    assert mcp.submit_calls == 0


def test_safety_mismatch_blocks_before_batch():
    mcp = FakeMCP(_health(paper_only=False))
    client = ExternalDecisionClient(mcp=mcp, adapter=StaticAdapter(_abstain_output()))

    result = client.run_once(run_id="r1")

    assert result["gate"] == "SAFETY_BLOCK"
    assert result["submitted"] is False
    assert mcp.batch_calls == 0
    assert mcp.submit_calls == 0


def test_malformed_model_output_fails_closed_without_submit():
    mcp = FakeMCP()
    client = ExternalDecisionClient(mcp=mcp, adapter=StaticAdapter("not-json"))

    with pytest.raises(DecisionOutputError):
        client.run_once(run_id="r1")

    assert mcp.batch_calls == 1
    assert mcp.submit_calls == 0


def test_forbidden_direction_or_flip_fields_are_rejected():
    packets = _packets()
    bad = _abstain_output()
    bad["decisions"][0]["direction"] = "LONG"

    with pytest.raises(DecisionOutputError, match="forbidden|unknown"):
        normalize_decisions(bad, packets)

    bad = _abstain_output()
    bad["decisions"][0]["flip"] = True
    with pytest.raises(DecisionOutputError, match="forbidden|unknown"):
        normalize_decisions(bad, packets)


@pytest.mark.parametrize(
    "value",
    [
        {"decisions": [{"packet_id": "pkt-1", "action": "ABSTAIN", "reason_codes": ["X"]}]},
        {
            "decisions": [
                {"packet_id": "pkt-2", "action": "ABSTAIN", "reason_codes": ["X"]},
                {"packet_id": "pkt-1", "action": "ABSTAIN", "reason_codes": ["X"]},
            ]
        },
    ],
)
def test_output_cardinality_and_order_must_match_packet_prefix(value):
    with pytest.raises(DecisionOutputError):
        normalize_decisions(value, _packets())


def test_normalization_adds_stable_idempotency_keys_without_direction():
    first = normalize_decisions(_abstain_output(), _packets())
    second = normalize_decisions(_abstain_output(), _packets())

    assert first == second
    assert all(item["idempotency_key"] for item in first)
    assert all(set(item) == {"packet_id", "action", "reason_codes", "idempotency_key"} for item in first)


def test_exactly_one_batch_and_one_submit_per_run():
    mcp = FakeMCP()
    adapter = StaticAdapter(_abstain_output())
    client = ExternalDecisionClient(mcp=mcp, adapter=adapter, batch_limit=2)

    result = client.run_once(run_id="r1")

    assert result["gate"] == "READY"
    assert result["submitted"] is True
    assert result["applied"] == 2
    assert mcp.health_calls == 1
    assert mcp.batch_calls == 1
    assert mcp.submit_calls == 1
    assert adapter.calls == 1


def test_mcp_client_repr_never_exposes_bearer():
    secret = "s" * 48
    client = MCPJSONRPCClient("https://example.invalid/mcp", token=secret)
    assert secret not in repr(client)


def test_openai_compatible_adapter_has_no_provider_sdk_and_sends_only_packets():
    captured = {}

    def fake_post(url, headers, payload, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["payload"] = payload
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(_abstain_output(), separators=(",", ":"))
                    }
                }
            ]
        }

    adapter = OpenAICompatibleDecisionAdapter(
        base_url="https://llm.example/v1",
        model="model-x",
        api_key="k" * 48,
        http_post=fake_post,
    )
    result = adapter.decide(_packets(), run_id="run-x")

    assert result == _abstain_output()
    assert captured["url"] == "https://llm.example/v1/chat/completions"
    wire = json.dumps(captured["payload"], sort_keys=True)
    assert "pkt-1" in wire and "pkt-2" in wire
    assert "current_market" not in wire
    assert "settlement" not in wire
    assert "outcome" not in wire
    assert "run_id" not in wire
    assert "run-x" not in wire


def test_mcp_error_traceback_does_not_leak_bearer():
    secret = "mcp-secret-" + ("x" * 48)

    def bad_post(url, headers, payload, timeout):
        raise RuntimeError(secret)

    client = MCPJSONRPCClient(
        "https://example.invalid/mcp",
        token="t" * 48,
        http_post=bad_post,
    )
    with pytest.raises(MCPUnavailable) as caught:
        client.get_gptrader_health()

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert secret not in rendered


def test_provider_error_traceback_does_not_leak_api_key():
    secret = "provider-secret-" + ("y" * 48)

    def bad_post(url, headers, payload, timeout):
        raise RuntimeError(secret)

    adapter = OpenAICompatibleDecisionAdapter(
        base_url="https://llm.example/v1",
        model="model-x",
        api_key=secret,
        http_post=bad_post,
    )
    with pytest.raises(MCPUnavailable) as caught:
        adapter.decide(_packets(), run_id="r-secret")

    rendered = "".join(
        traceback.format_exception(
            type(caught.value),
            caught.value,
            caught.value.__traceback__,
        )
    )
    assert secret not in rendered


def test_schema_version_mismatch_blocks_before_batch():
    mcp = FakeMCP(_health(schema_version="gptrader.decision.v2"))
    client = ExternalDecisionClient(mcp=mcp, adapter=StaticAdapter(_abstain_output()))

    result = client.run_once(run_id="r-schema")

    assert result["gate"] == "SAFETY_BLOCK"
    assert result["submitted"] is False
    assert mcp.batch_calls == 0
    assert mcp.submit_calls == 0


def test_env_factory_builds_vendor_neutral_client(monkeypatch):
    factory = getattr(external_client_module, "create_external_client_from_env", None)
    assert callable(factory), "environment factory is required for schedulable deployment"

    monkeypatch.setenv("SENEX_GPTRADER_MCP_URL", "https://mcp.example/mcp")
    monkeypatch.setenv("SENEX_GPTRADER_MCP_TOKEN", "m" * 48)
    monkeypatch.setenv("SENEX_DECISION_PROVIDER_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("SENEX_DECISION_PROVIDER_MODEL", "model-x")
    monkeypatch.setenv("SENEX_DECISION_PROVIDER_API_KEY", "p" * 48)
    monkeypatch.setenv("SENEX_GPTRADER_BATCH_LIMIT", "4")

    client = factory()

    assert isinstance(client, ExternalDecisionClient)
    direct_cls = getattr(external_client_module, "DirectHTTPDecisionClient", None)
    assert direct_cls is not None, "direct HTTP Decision client is required"
    assert isinstance(client.mcp, direct_cls)
    assert isinstance(client.adapter, OpenAICompatibleDecisionAdapter)
    assert client.batch_limit == 4
    assert "m" * 48 not in repr(client.mcp)
    assert "p" * 48 not in repr(client.adapter)


def test_main_is_one_shot_schedulable_entrypoint(monkeypatch, capsys):
    main = getattr(external_client_module, "main", None)
    assert callable(main), "one-shot CLI entrypoint is required for scheduling"

    class OneShot:
        def run_once(self, *, run_id):
            assert run_id == "r-main"
            return {"gate": "READY", "submitted": False, "applied": 0}

    monkeypatch.setattr(
        external_client_module,
        "create_external_client_from_env",
        lambda: OneShot(),
    )
    code = main(["--run-id", "r-main"])
    captured = capsys.readouterr()

    assert code == 0
    assert json.loads(captured.out) == {
        "applied": 0,
        "gate": "READY",
        "submitted": False,
    }



def test_direct_http_client_reuses_existing_mcp_url_and_token():
    direct_cls = getattr(external_client_module, "DirectHTTPDecisionClient", None)
    assert direct_cls is not None, "direct HTTP Decision client is required"

    calls = []

    def fake_get(url, headers, timeout):
        calls.append(("GET", url, dict(headers)))
        if url.endswith("/v1/health"):
            return {
                "status": "ok",
                "service": "senex-gptrader-direct-http",
                "paper_only": True,
                "simulation_only": True,
                "live": False,
            }
        assert "/v1/predictions/next" in url
        return {
            "cursor_in": "c0",
            "next_cursor": "c1",
            "has_more": False,
            "packets": [{"packet_id": "pkt-1"}],
        }

    def fake_post(url, headers, payload, timeout):
        calls.append(("POST", url, dict(headers), payload))
        return {"applied": 1, "duplicate": False, "cursor": "c1"}

    secret = "z" * 48
    client = direct_cls(
        "https://mcp.example/mcp",
        token=secret,
        http_get=fake_get,
        http_post=fake_post,
    )

    health = client.get_gptrader_health()
    assert health == {
        "ready": True,
        "paper_only": True,
        "simulation_only": True,
        "live": False,
        "schema_version": "gptrader.decision.v1",
        "cursor": None,
    }

    batch = client.get_prediction_batch(cursor=None, limit=1)
    assert batch["packets"] == [{"packet_id": "pkt-1"}]

    submit = client.submit_paper_decisions(
        "run-direct",
        "c0",
        [
            {
                "packet_id": "pkt-1",
                "action": "ABSTAIN",
                "reason_codes": ["DIRECT"],
                "idempotency_key": "idem-direct",
            }
        ],
    )
    assert submit["applied"] == 1
    assert calls[0][0:2] == ("GET", "https://mcp.example/v1/health")
    assert calls[1][0] == "GET"
    assert calls[1][1] == "https://mcp.example/v1/predictions/next?limit=1"
    assert calls[2][0:2] == ("POST", "https://mcp.example/v1/decisions")
    assert all(call[2]["Authorization"] == f"Bearer {secret}" for call in calls)
    assert secret not in repr(client)


def test_direct_http_client_encodes_cursor_and_fails_closed_on_bad_health():
    direct_cls = getattr(external_client_module, "DirectHTTPDecisionClient", None)
    assert direct_cls is not None, "direct HTTP Decision client is required"

    seen = []

    def fake_get(url, headers, timeout):
        seen.append(url)
        if url.endswith("/v1/health"):
            return {
                "status": "degraded",
                "paper_only": True,
                "simulation_only": True,
                "live": False,
            }
        return {
            "cursor_in": "c x",
            "next_cursor": "c2",
            "has_more": False,
            "packets": [],
        }

    client = direct_cls(
        "https://mcp.example/mcp",
        token="t" * 48,
        http_get=fake_get,
    )
    health = client.get_gptrader_health()
    assert health["ready"] is False

    client.get_prediction_batch(cursor="c x", limit=2)
    assert seen[-1] == "https://mcp.example/v1/predictions/next?cursor=c+x&limit=2"
