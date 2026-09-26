"""SENEX B8.1 runtime provenance facade over one internal artifact identity."""
from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any

from .artifact_identity import read_artifact_identity

_SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")


def _env_first(*names: str) -> str | None:
    for name in names:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value
    return None


def _provider_oci_attestation() -> dict[str, Any]:
    image_digest = _env_first("SENEX_IMAGE_DIGEST", "NORTHFLANK_IMAGE_DIGEST")
    return {
        "image_digest": image_digest,
        "syntax_valid": bool(image_digest and _SHA256.fullmatch(image_digest.lower())),
        "self_proof": False,
        "role": "EXTERNAL_ATTESTATION_ONLY",
    }


def runtime_provenance() -> dict[str, Any]:
    identity = read_artifact_identity()
    provider_oci = _provider_oci_attestation()
    return {
        "contract": "senex-runtime-provenance-v1",
        "source_commit": identity["source_commit"],
        "source_tree": identity["source_tree"],
        "image_digest": provider_oci["image_digest"],
        "build_digest": identity["build_digest"],
        "declared_build_digest": identity.get("declared_build_digest") or identity["build_digest"],
        "computed_build_digest": identity["computed_build_digest"],
        "checks": dict(identity["checks"]),
        "exact": bool(identity["exact"]),
        "artifact_identity": identity,
        "provider_oci_attestation": provider_oci,
    }


def provenance_fingerprint(payload: dict[str, Any] | None = None) -> str:
    payload = payload or runtime_provenance()
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()
