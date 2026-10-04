"""Offline-only NVIDIA DeepSeek V4.1 Flash compatibility profile for SENEX.

This module is intentionally unable to perform a real provider network call from
its CLI.  It validates the existing OpenAI-compatible SENEX adapter only when a
test injects a fake transport.

Why: the owner has not authorized any paid/cost-incurring external-provider
operation.  A future live provider probe therefore requires a separate reviewed
code change; possessing NVIDIA_API_KEY is not sufficient.
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
    """Return generic SENEX provider env values without persisting secrets."""
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
    """Validate provider wire/schema through an injected non-network transport."""
    key = str(api_key or "").strip()
    if not key:
        raise RuntimeError(f"{NVIDIA_API_KEY_ENV} is required")
    if http_post is None:
        raise RuntimeError(
            "external provider network disabled by zero-cost owner lock"
        )

    adapter = OpenAICompatibleDecisionAdapter(
        base_url=NVIDIA_BASE_URL,
        model=NVIDIA_MODEL,
        api_key=key,
        timeout=30.0,
        http_post=http_post,
    )
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
        "network_used": False,
        "packet_id": decision["packet_id"],
        "action": decision["action"],
        "reason_codes": decision["reason_codes"],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Inspect the NVIDIA DeepSeek V4.1 Flash SENEX profile. "
            "Real provider network calls are disabled by owner policy."
        )
    )
    parser.parse_args(argv)

    key_present = bool(str(os.environ.get(NVIDIA_API_KEY_ENV) or "").strip())
    payload = {
        "status": (
            "BLOCKED_EXTERNAL_PROVIDER_NETWORK_DISABLED"
            if key_present
            else "BLOCKED_NO_NVIDIA_API_KEY"
        ),
        "provider": "NVIDIA_NIM",
        "model": NVIDIA_MODEL,
        "paper_only": True,
        "simulation_only": True,
        "mcp_contacted": False,
        "decision_submitted": False,
        "network_used": False,
        "zero_cost_owner_lock": True,
    }
    print(json.dumps(payload, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
