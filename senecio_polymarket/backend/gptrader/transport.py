from __future__ import annotations

import json
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .cursor import PacketCursor
from .sealer import PacketSealer, canonical_json

INGEST_URL_ENV = "SENEX_GPTRADER_INGEST_URL"
INGEST_TOKEN_ENV = "SENEX_GPTRADER_INGEST_TOKEN"
MAX_REPLICATION_BATCH = 16


class PacketReplicationError(RuntimeError):
    pass


def _cursor_path(sealer: PacketSealer) -> Path:
    return sealer.paths.root / "replication_cursor.json"


def _read_cursor(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        seq = value.get("packet_seq")
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ValueError
        return seq
    except (OSError, json.JSONDecodeError, AttributeError, ValueError) as exc:
        raise PacketReplicationError("replication cursor is invalid") from exc


def _write_cursor(path: Path, packet_seq: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{threading.get_ident()}")
    payload = canonical_json({"packet_seq": packet_seq}) + "\n"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _validate_endpoint(endpoint: str) -> str:
    parsed = urlparse(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/ingest/t0"
    ):
        raise PacketReplicationError("ingest endpoint must be dedicated HTTPS /ingest/t0")
    return endpoint.rstrip("/")


def _default_post_json(endpoint: str, token: str, packet: dict[str, Any]) -> dict[str, Any]:
    body = json.dumps(
        {"packet": packet},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as exc:
        raise PacketReplicationError(f"consumer unavailable: {type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise PacketReplicationError("consumer acknowledgement is invalid")
    return value


def replicate_pending_t0(
    *,
    root: str | Path | None = None,
    endpoint: str | None = None,
    token: str | None = None,
    post_json: Callable[[str, str, dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_endpoint = endpoint or os.environ.get(INGEST_URL_ENV)
    resolved_token = token or os.environ.get(INGEST_TOKEN_ENV)
    if not resolved_endpoint and not resolved_token:
        return {"enabled": False, "replicated": 0}
    if not resolved_endpoint or not resolved_token:
        raise PacketReplicationError("ingest endpoint and token must be configured together")
    if len(resolved_token) < 32:
        raise PacketReplicationError("ingest token must be at least 32 characters")

    resolved_endpoint = _validate_endpoint(resolved_endpoint)
    sender = post_json or _default_post_json
    sealer = PacketSealer(root=root)
    cursor_path = _cursor_path(sealer)
    cursor_seq = _read_cursor(cursor_path)
    packets = sealer.read_after(PacketCursor(cursor_seq), limit=MAX_REPLICATION_BATCH)

    replicated = 0
    for packet in packets:
        try:
            acknowledgement = sender(resolved_endpoint, resolved_token, packet)
        except PacketReplicationError:
            raise
        except Exception as exc:
            raise PacketReplicationError(f"consumer unavailable: {type(exc).__name__}") from exc

        if (
            acknowledgement.get("packet_id") != packet.get("packet_id")
            or acknowledgement.get("packet_seq") != packet.get("packet_seq")
            or acknowledgement.get("packet_hash") != packet.get("packet_hash")
        ):
            raise PacketReplicationError("consumer acknowledgement mismatch")

        cursor_seq = int(packet["packet_seq"])
        _write_cursor(cursor_path, cursor_seq)
        replicated += 1

    return {
        "enabled": True,
        "replicated": replicated,
        "packet_seq": cursor_seq,
        "has_more": len(packets) == MAX_REPLICATION_BATCH,
    }
