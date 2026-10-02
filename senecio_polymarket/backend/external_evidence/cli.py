from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters import (
    AgentReachBridgeCollector,
    PatchrightBridgeCollector,
    ScraplingCollector,
)
from .paths import ExternalEvidencePaths
from .service import ShadowEvidenceService


def _collector(name: str):
    if name == "agent-reach":
        return AgentReachBridgeCollector.from_env()
    if name == "patchright":
        return PatchrightBridgeCollector.from_env()
    if name == "scrapling":
        return ScraplingCollector.from_env()
    raise ValueError(f"unsupported provider: {name}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="senex-external-evidence")
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="Capture one public URL into the shadow journal")
    collect.add_argument("--provider", choices=["agent-reach", "patchright", "scrapling"], required=True)
    collect.add_argument("--url", required=True)
    collect.add_argument("--timeout", type=float, default=15.0)
    collect.add_argument("--root")

    verify = sub.add_parser("verify", help="Verify journal hash chain and raw blobs")
    verify.add_argument("--root")

    args = parser.parse_args()
    paths = ExternalEvidencePaths.from_root(Path(args.root)) if args.root else ExternalEvidencePaths.default()
    service = ShadowEvidenceService(paths)

    if args.command == "verify":
        print(json.dumps(service.journal.verify_chain(), sort_keys=True))
        return 0

    result = service.collect_and_persist(
        _collector(args.provider),
        args.url,
        timeout=float(args.timeout),
    )
    print(
        json.dumps(
            {
                "contract": "senex.external_evidence.capture_result.v1",
                "provider": args.provider,
                "appended": result.appended,
                "event_id": result.event_id,
                "record_hash": result.record_hash,
                "shadow_only": True,
                "decision_allowed": False,
                "t0_allowed": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
