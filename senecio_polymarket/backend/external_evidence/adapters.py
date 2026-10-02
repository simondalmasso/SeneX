from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from .schema import MAX_RAW_BYTES, EvidenceCapture
from .security import validate_public_url

BridgeRunner = Callable[[list[str], str, float], tuple[int, str, str]]
CLIFileRunner = Callable[[list[str], float], tuple[int, str, str]]


class BridgeUnavailable(RuntimeError):
    pass


class CollectorError(RuntimeError):
    pass


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _bridge_runner(argv: list[str], stdin_text: str, timeout: float) -> tuple[int, str, str]:
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    if proc.stdin is None or proc.stdout is None:
        proc.kill()
        proc.wait()
        raise CollectorError("bridge process pipes unavailable")

    state: dict[str, object] = {"overflow": False, "chunks": []}

    def _read_stdout() -> None:
        chunks: list[bytes] = []
        total = 0
        try:
            while True:
                chunk = proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_RAW_BYTES:
                    state["overflow"] = True
                    proc.kill()
                    break
                chunks.append(chunk)
        finally:
            state["chunks"] = chunks

    reader = threading.Thread(target=_read_stdout, name="senex-bridge-stdout", daemon=True)
    reader.start()
    try:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass
        try:
            return_code = proc.wait(timeout=float(timeout))
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
    finally:
        reader.join(timeout=1.0)
        proc.stdout.close()

    if bool(state["overflow"]):
        raise CollectorError("bridge response exceeds bound")

    raw_stdout = b"".join(state["chunks"])
    try:
        stdout = raw_stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CollectorError("bridge response must be UTF-8") from exc
    return int(return_code), stdout, ""


def _cli_runner(argv: list[str], timeout: float) -> tuple[int, str, str]:
    proc = subprocess.run(
        argv,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=timeout,
        shell=False,
    )
    return int(proc.returncode), "", ""


class _JSONBridgeCollector:
    provider = "bridge"
    env_name = ""

    def __init__(self, executable: str, *, runner: BridgeRunner = _bridge_runner):
        target = str(executable or "").strip()
        if not target:
            raise BridgeUnavailable(f"{self.provider} bridge executable is not configured")
        self.executable = target
        self.runner = runner

    @classmethod
    def from_env(cls):
        value = (os.environ.get(cls.env_name) or "").strip()
        if not value:
            raise BridgeUnavailable(f"{cls.env_name} is not configured")
        return cls(value)

    def collect(self, url: str, *, timeout: float = 15.0) -> EvidenceCapture:
        source_url = validate_public_url(url)
        request = {
            "contract": "senex.external_evidence.bridge_request.v1",
            "url": source_url,
            "shadow_only": True,
            "decision_allowed": False,
            "t0_allowed": False,
        }
        rc, stdout, _stderr = self.runner(
            [self.executable],
            json.dumps(request, sort_keys=True, separators=(",", ":")),
            float(timeout),
        )
        if rc != 0:
            raise CollectorError(f"{self.provider} bridge failed closed")
        if len(stdout.encode("utf-8")) > MAX_RAW_BYTES:
            raise CollectorError(f"{self.provider} bridge response exceeds bound")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise CollectorError(f"{self.provider} bridge returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise CollectorError(f"{self.provider} bridge returned non-object JSON")
        content_value = payload.get("content")
        if content_value is None:
            content = ""
        elif isinstance(content_value, str):
            content = content_value
        else:
            raise CollectorError(f"{self.provider} bridge content must be UTF-8 text")
        raw_value = payload.get("raw", content)
        if not isinstance(raw_value, str):
            raise CollectorError(f"{self.provider} bridge raw must be UTF-8 text")
        metadata = payload.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise CollectorError(f"{self.provider} bridge metadata must be an object")
        return EvidenceCapture(
            provider=self.provider,
            collector=f"{self.provider}-json-bridge",
            source_kind=str(payload.get("source_kind") or "web"),
            source_url=source_url,
            native_id=str(payload["native_id"]) if payload.get("native_id") is not None else None,
            published_at=(
                str(payload["published_at"]) if payload.get("published_at") is not None else None
            ),
            observed_at=str(payload.get("observed_at") or _utcnow()),
            raw=raw_value.encode("utf-8"),
            content=content,
            provider_version=str(payload.get("provider_version") or "unknown"),
            metadata=metadata,
        )


class AgentReachBridgeCollector(_JSONBridgeCollector):
    """Bridge for Agent-Reach channel tooling.

    Agent-Reach itself is primarily an installer/skill surface rather than one stable
    read command. SENEX therefore requires a tiny owner-controlled executable that
    emits the strict bridge JSON contract on stdout. Cookies/tokens stay outside SENEX.
    """

    provider = "agent_reach"
    env_name = "SENEX_AGENT_REACH_BRIDGE"


class PatchrightBridgeCollector(_JSONBridgeCollector):
    """Bridge for Patchright Enhanced/GhostProbe extraction.

    The Node/browser implementation remains out-of-process. SENEX receives only a
    bounded normalized public capture and never browser state/cookies.
    """

    provider = "patchright_enhanced"
    env_name = "SENEX_PATCHRIGHT_BRIDGE"


class ScraplingCollector:
    """Optional zero-coupling adapter over Scrapling's documented CLI."""

    def __init__(
        self,
        executable: str = "scrapling",
        *,
        runner: CLIFileRunner = _cli_runner,
    ):
        target = str(executable or "").strip()
        if not target:
            raise BridgeUnavailable("Scrapling executable is not configured")
        self.executable = target
        self.runner = runner

    @classmethod
    def from_env(cls):
        configured = (os.environ.get("SENEX_SCRAPLING_BIN") or "scrapling").strip()
        if not configured:
            raise BridgeUnavailable("SENEX_SCRAPLING_BIN is empty")
        if os.path.sep not in configured and shutil.which(configured) is None:
            raise BridgeUnavailable("Scrapling CLI is not installed")
        return cls(configured)

    def collect(self, url: str, *, timeout: float = 15.0) -> EvidenceCapture:
        source_url = validate_public_url(url)
        with tempfile.TemporaryDirectory(prefix="senex-ext-") as tmp:
            output = Path(tmp) / "capture.txt"
            argv = [
                self.executable,
                "extract",
                "get",
                source_url,
                str(output),
                "--ai-targeted",
                "--no-follow-redirects",
                "--timeout",
                str(max(1, int(timeout))),
            ]
            rc, _stdout, _stderr = self.runner(argv, float(timeout))
            if rc != 0 or not output.is_file():
                raise CollectorError("Scrapling capture failed closed")
            if output.stat().st_size > MAX_RAW_BYTES:
                raise CollectorError("Scrapling capture exceeds raw bound")
            raw = output.read_bytes()
        content = raw.decode("utf-8", errors="replace")
        return EvidenceCapture(
            provider="scrapling",
            collector="scrapling-cli",
            source_kind="web",
            source_url=source_url,
            native_id=None,
            published_at=None,
            observed_at=_utcnow(),
            raw=raw,
            content=content,
            provider_version="cli",
            metadata={"mode": "extract-get", "ai_targeted": True},
        )
