"""Isolated NVIDIA DeepSeek V4.1 Flash compatibility probe for SENEX.

This file never talks to SENEX MCP, never submits decisions, and never changes
runtime state. It exercises the already-existing vendor-neutral
OpenAICompatibleDecisionAdapter against one synthetic sealed packet.

A successful probe proves provider wire compatibility only. It does not prove
trading edge and does not authorize scheduling, LIVE, orders, or capital.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Mapping

from senecio_polymarket.backend.gptrader.external_client import (
    OpenAICompatibleDecisionAdapter,
    normalize_decisions,
)

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
NVIDIA_MODEL = "deepseek-ai/deepseek-v4.1-flash"
NVIDIA_API_KEY_ENV = "NVIDIA_API_KEY"

PROBE_PACKET = {
    "packet_id": "nvidia-deepseek-v41-probe-1",
    "packet_seq": 1,
    "symbol": "BTCUSDT",
    "prediction": "LONG",
    "confidence": 0.51,
    "paper_only": True,
    "simulation_only": True,
    "synthetic_probe": True,
}


def provider_overrides(
    environ: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Return generic SENEX provider env values without copying secrets to disk."""
    env = os.environ if environ is None else environ
    key = str(env.get(NVIDIA_API_KEY_ENV) or "").strip()
    if not key:
        raise RuntimeError(f"{NVIDIA_API_KEY_ENV} is required")
    return {
        "SENEX_DECISION_PROVIDER_BASE_URL": NVIDIA_BASE_URL,
        "SENEX_DECISION_PROVIDER_MODEL": NVIDIA_MODEL,
        "SENEX_DECISION_PROVIDER_API_KEY": key,
    }


def run_probe(
    *,
    api_key: str,
    http_post=None,
) -> dict[str, Any]:
    """Run one provider-only synthetic decision and validate SENEX's strict schema."""
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError(f"{NVIDIA_API_KEY_ENV} is required")

    kwargs: dict[str, Any] = {
        "base_url": NVIDIA_BASE_URL,
        "model": NVIDIA_MODEL,
        "api_key": key,
        "timeout": 30.0,
    }
    if http_post is not None:
        kwargs["http_post"] = http_post

    adapter = OpenAICompatibleDecisionAdapter(**kwargs)
    raw = adapter.decide([dict(PROBE_PACKET)], run_id="NVIDIA_V41_PROBE")
    normalized = normalize_decisions(raw, [dict(PROBE_PACKET)])
    decision = normalized[0]
    return {
        "status": "PASS",
        "provider": "NVIDIA_NIM",
        "base_url": NVIDIA_BASE_URL,
        "model": NVIDIA_MODEL,
        "paper_only": True,
        "simulation_only": True,
        "mcp_contacted": False,
        "decision_submitted": False,
        "packet_id": decision["packet_id"],
        "action": decision["action"],
        "reason_codes": decision["reason_codes"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Probe NVIDIA DeepSeek V4.1 Flash without touching SENEX MCP."
    )
    parser.parse_args(argv)
    key = str(os.environ.get(NVIDIA_API_KEY_ENV) or "").strip()
    if not key:
        print(json.dumps({
            "status": "BLOCKED_NO_NVIDIA_API_KEY",
            "provider": "NVIDIA_NIM",
            "model": NVIDIA_MODEL,
            "mcp_contacted": False,
            "decision_submitted": False,
        }, sort_keys=True))
        return 2

    try:
        result = run_probe(api_key=key)
    except Exception as exc:
        print(json.dumps({
            "status": "FAIL",
            "error": type(exc).__name__,
            "provider": "NVIDIA_NIM",
            "model": NVIDIA_MODEL,
            "mcp_contacted": False,
            "decision_submitted": False,
        }, sort_keys=True))
        return 1

    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
