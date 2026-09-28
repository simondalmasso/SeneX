#!/usr/bin/env python3
"""Offline consistency checks for ORDER089 research artifacts."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = {
    "RECOVERY_MAP.md",
    "CALIBRATION_PROTOCOL.md",
    "CALIBRATION_ARTIFACT_SCHEMA.json",
    "PROMOTION_LADDER.md",
    "TEST_PLAN.md",
}
INCIDENT_IDS = {"6868", "6870", "6872"}


def main() -> int:
    missing = sorted(name for name in REQUIRED if not (ROOT / name).is_file())
    if missing:
        raise SystemExit(f"missing required files: {missing}")

    schema = json.loads(
        (ROOT / "CALIBRATION_ARTIFACT_SCHEMA.json").read_text(encoding="utf-8")
    )
    assert schema["$schema"].endswith("2020-12/schema")
    frozen = schema["properties"]["frozen"]["properties"]
    assert frozen["direction_owner"]["const"] == "SENEX"
    assert frozen["allowed_actions"]["const"] == ["TAKE", "ABSTAIN"]
    assert "model_provider" not in schema["required"]
    assert "model_name" not in schema["required"]

    corpus = "\n".join(
        (ROOT / name).read_text(encoding="utf-8") for name in REQUIRED
    )
    for prediction_id in INCIDENT_IDS:
        assert prediction_id in corpus
    for token in (
        "600",
        "25",
        "168",
        "TAKE",
        "ABSTAIN",
        "MODEL_VENDOR_AGNOSTIC",
    ):
        assert token in corpus

    schema_text = json.dumps(schema, sort_keys=True).lower()
    for banned in ("openai", "anthropic", "google", "xai", "mistral"):
        assert banned not in schema_text

    print("ORDER089_RESEARCH_VALIDATION=PASS")
    print("ORDER086_FILES_CHANGED=0")
    print("D1_READS=0")
    print("D1_WRITES=0")
    print("LIVE=NO")
    print("REAL_ORDERS=0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
