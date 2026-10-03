from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import tempfile
import time
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


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass


def _bridge_runner(argv: list[str], stdin_text: str, timeout: float) -> tuple[int, str, str]:
    """Run a bridge with bounded stdout and a wall-clock deadline.

    A dedicated process group ensures browser/helper descendants inheriting stdout
    cannot keep the pipe open forever after the direct bridge process exits.
    """
    timeout_s = float(timeout)
    if timeout_s <= 0:
        raise ValueError("bridge timeout must be positive")

    proc = subprocess.Popen(
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        shell=False,
        bufsize=0,
        start_new_session=(os.name == "posix"),
    )
    if proc.stdin is None or proc.stdout is None:
        _kill_process_group(proc)
        proc.wait()
        raise CollectorError("bridge process pipes unavailable")

    try:
        try:
            proc.stdin.write(stdin_text.encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass

        fd = proc.stdout.fileno()
        os.set_blocking(fd, False)
        selector = selectors.DefaultSelector()
        selector.register(fd, selectors.EVENT_READ)
        chunks: list[bytes] = []
        total = 0
        eof = False
        deadline = time.monotonic() + timeout_s

        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    _kill_process_group(proc)
                    try:
                        proc.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        pass
                    raise subprocess.TimeoutExpired(argv, timeout_s)

                events = selector.select(timeout=min(0.05, remaining))
                if events:
                    while True:
                        try:
                            allowance = max(1, MAX_RAW_BYTES - total + 1)
                            chunk = os.read(fd, min(64 * 1024, allowance))
                        except BlockingIOError:
                            break
                        if not chunk:
                            eof = True
                            try:
                                selector.unregister(fd)
                            except Exception:
                                pass
                            break
                        total += len(chunk)
                        if total > MAX_RAW_BYTES:
                            _kill_process_group(proc)
                            try:
                                proc.wait(timeout=1.0)
                            except subprocess.TimeoutExpired:
                                pass
                            raise CollectorError("bridge response exceeds bound")
                        chunks.append(chunk)

                if eof:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        _kill_process_group(proc)
                        try:
                            proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            pass
                        raise subprocess.TimeoutExpired(argv, timeout_s)
                    try:
                        return_code = proc.wait(timeout=remaining)
                    except subprocess.TimeoutExpired:
                        _kill_process_group(proc)
                        try:
                            proc.wait(timeout=1.0)
                        except subprocess.TimeoutExpired:
                            pass
                        raise subprocess.TimeoutExpired(argv, timeout_s)
                    break

                # If the direct process exited but a descendant inherited stdout,
                # keep waiting for pipe EOF only until the same wall-clock deadline.
                if proc.poll() is not None and not events:
                    continue

            if not eof:
                return_code = proc.wait(timeout=max(0.001, deadline - time.monotonic()))
        finally:
            try:
                selector.unregister(fd)
            except Exception:
                pass
            selector.close()
            proc.stdout.close()

        raw_stdout = b"".join(chunks)
        try:
            stdout = raw_stdout.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CollectorError("bridge response must be UTF-8") from exc
        return int(return_code), stdout, ""
    except Exception:
        if proc.poll() is None:
            _kill_process_group(proc)
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        raise


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


def _payload_string(
    payload: dict,
    key: str,
    *,
    default: str | None = None,
    nullable: bool = False,
) -> str | None:
    if key not in payload or payload[key] is None:
        if nullable:
            return None
        return default
    value = payload[key]
    if not isinstance(value, str):
        raise CollectorError(f"bridge field {key} must be a string")
    return value


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
        metadata_value = payload.get("metadata")
        if metadata_value is None:
            metadata = {}
        elif isinstance(metadata_value, dict):
            metadata = metadata_value
        else:
            raise CollectorError(f"{self.provider} bridge metadata must be an object")

        source_kind = _payload_string(payload, "source_kind", default="web")
        native_id = _payload_string(payload, "native_id", nullable=True)
        published_at = _payload_string(payload, "published_at", nullable=True)
        observed_at = _payload_string(payload, "observed_at", default=_utcnow())
        provider_version = _payload_string(payload, "provider_version", default="unknown")
        assert source_kind is not None
        assert observed_at is not None
        assert provider_version is not None

        return EvidenceCapture(
            provider=self.provider,
            collector=f"{self.provider}-json-bridge",
            source_kind=source_kind,
            source_url=source_url,
            native_id=native_id,
            published_at=published_at,
            observed_at=observed_at,
            raw=raw_value.encode("utf-8"),
            content=content,
            provider_version=provider_version,
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
