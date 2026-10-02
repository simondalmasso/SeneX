from __future__ import annotations

import json
from pathlib import Path

import pytest

from senecio_polymarket.backend.external_evidence.adapters import (
    AgentReachBridgeCollector,
    BridgeUnavailable,
    PatchrightBridgeCollector,
    ScraplingCollector,
)
from senecio_polymarket.backend.external_evidence.journal import ExternalEvidenceJournal
from senecio_polymarket.backend.external_evidence.paths import ExternalEvidencePaths
from senecio_polymarket.backend.external_evidence.schema import (
    MAX_CONTENT_BYTES,
    EvidenceCapture,
    EvidenceValidationError,
)
from senecio_polymarket.backend.external_evidence.security import (
    UnsafeTargetError,
    validate_public_url,
    validate_resolved_public_url,
)
from senecio_polymarket.backend.external_evidence.service import ShadowEvidenceService


def _capture(**overrides):
    payload = dict(
        provider="unit",
        collector="unit-test",
        source_kind="web",
        source_url="https://example.com/post/1",
        native_id="post-1",
        published_at="2026-10-02T10:00:00Z",
        observed_at="2026-10-02T10:00:01Z",
        raw=b"<html><body>hello</body></html>",
        content="hello",
        provider_version="1",
        metadata={"language": "en"},
    )
    payload.update(overrides)
    return EvidenceCapture(**payload)


def test_results_dir_override_keeps_external_evidence_on_durable_root(tmp_path, monkeypatch):
    monkeypatch.setenv("SENEX_RESULTS_DIR", str(tmp_path))
    paths = ExternalEvidencePaths.default()
    assert paths.root == tmp_path / "external_evidence"
    assert paths.journal == paths.root / "events.jsonl"
    assert paths.blobs == paths.root / "blobs"


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a",
        "http://localhost/a",
        "http://10.1.2.3/a",
        "http://172.16.0.5/a",
        "http://192.168.1.5/a",
        "http://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
        "ftp://example.com/file",
        "https://user:pass@example.com/",
        "https://[::1]/",
    ],
)
def test_target_guard_rejects_private_local_non_http_and_credentials(url):
    with pytest.raises(UnsafeTargetError):
        validate_public_url(url)


def test_target_guard_accepts_public_https():
    assert validate_public_url("https://www.reddit.com/r/Bitcoin/") == "https://www.reddit.com/r/Bitcoin/"


def test_resolved_target_guard_rejects_dns_to_private_ip():
    def fake_resolver(host, port, type=0):
        assert host == "public-name.example"
        return [(2, 1, 6, "", ("10.10.0.8", port))]

    with pytest.raises(UnsafeTargetError, match="resolved"):
        validate_resolved_public_url("https://public-name.example/a", resolver=fake_resolver)


def test_resolved_target_guard_accepts_public_ip():
    def fake_resolver(host, port, type=0):
        return [(2, 1, 6, "", ("93.184.216.34", port))]

    assert (
        validate_resolved_public_url("https://example.com/a", resolver=fake_resolver)
        == "https://example.com/a"
    )


def test_capture_rejects_future_source_time():
    with pytest.raises(EvidenceValidationError, match="published_at"):
        _capture(published_at="2026-10-02T10:10:00Z").to_event(
            captured_at="2026-10-02T10:00:02Z"
        )


def test_capture_rejects_publish_time_after_observation_cut():
    with pytest.raises(EvidenceValidationError, match="observed_at"):
        _capture(
            published_at="2026-10-02T10:04:00Z",
            observed_at="2026-10-02T10:00:00Z",
        ).to_event(captured_at="2026-10-02T10:04:01Z")


def test_capture_rejects_secret_bearing_metadata():
    with pytest.raises(EvidenceValidationError, match="sensitive metadata"):
        _capture(metadata={"nested": {"cookie": "should-never-persist"}}).to_event(
            captured_at="2026-10-02T10:00:02Z"
        )


def test_capture_rejects_oversized_normalized_content():
    with pytest.raises(EvidenceValidationError, match="content"):
        _capture(content="x" * (MAX_CONTENT_BYTES + 1)).to_event(
            captured_at="2026-10-02T10:00:02Z"
        )


def test_event_identity_is_deterministic_and_shadow_only():
    event_a = _capture().to_event(captured_at="2026-10-02T10:00:02Z")
    event_b = _capture().to_event(captured_at="2026-10-02T10:00:02Z")
    assert event_a.event_id == event_b.event_id
    assert event_a.content_sha256 == event_b.content_sha256
    assert event_a.raw_sha256 == event_b.raw_sha256
    assert event_a.shadow_only is True
    assert event_a.decision_allowed is False
    assert event_a.t0_allowed is False


def test_journal_is_append_only_deduplicated_and_hash_chained(tmp_path):
    paths = ExternalEvidencePaths.from_root(tmp_path)
    journal = ExternalEvidenceJournal(paths)

    first = _capture(native_id="a", content="alpha", raw=b"alpha").to_event(
        captured_at="2026-10-02T10:00:02Z"
    )
    second = _capture(native_id="b", content="beta", raw=b"beta").to_event(
        captured_at="2026-10-02T10:00:03Z"
    )

    r1 = journal.append(first, raw=b"alpha")
    dup = journal.append(first, raw=b"alpha")
    r2 = journal.append(second, raw=b"beta")

    assert r1.appended is True
    assert dup.appended is False
    assert r2.appended is True

    rows = [json.loads(line) for line in paths.journal.read_text().splitlines()]
    assert len(rows) == 2
    assert rows[0]["prev_record_hash"] is None
    assert rows[1]["prev_record_hash"] == rows[0]["record_hash"]
    assert journal.verify_chain() == {"ok": True, "records": 2}

    assert (paths.blobs / first.raw_sha256).read_bytes() == b"alpha"
    assert (paths.blobs / second.raw_sha256).read_bytes() == b"beta"


def test_journal_refuses_to_open_corrupt_existing_chain(tmp_path):
    paths = ExternalEvidencePaths.from_root(tmp_path)
    journal = ExternalEvidenceJournal(paths)
    event = _capture(native_id="tamper", raw=b"raw", content="raw").to_event(
        captured_at="2026-10-02T10:00:02Z"
    )
    journal.append(event, raw=b"raw")
    row = json.loads(paths.journal.read_text())
    row["content"] = "tampered"
    paths.journal.write_text(json.dumps(row) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="integrity"):
        ExternalEvidenceJournal(paths)


def test_verifier_rejects_tampered_blob_relpath_without_traversal(tmp_path):
    paths = ExternalEvidencePaths.from_root(tmp_path)
    journal = ExternalEvidenceJournal(paths)
    event = _capture(native_id="path", raw=b"safe", content="safe").to_event(
        captured_at="2026-10-02T10:00:02Z"
    )
    journal.append(event, raw=b"safe")
    row = json.loads(paths.journal.read_text())
    row["blob_relpath"] = "../../outside"
    unsigned = dict(row)
    unsigned.pop("record_hash", None)
    import hashlib
    row["record_hash"] = hashlib.sha256(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    paths.journal.write_text(json.dumps(row) + "\n", encoding="utf-8")

    assert ExternalEvidenceJournal.verify_file(paths)["reason"] == "INVALID_BLOB_PATH"


def test_shadow_service_persists_capture_without_decision_hook(tmp_path):
    service = ShadowEvidenceService(ExternalEvidencePaths.from_root(tmp_path))
    result = service.persist(_capture(), captured_at="2026-10-02T10:00:02Z")
    assert result.appended is True

    row = json.loads(service.paths.journal.read_text().strip())
    assert row["shadow_only"] is True
    assert row["decision_allowed"] is False
    assert row["t0_allowed"] is False
    assert "prediction" not in row
    assert "action" not in row


def test_agent_reach_bridge_is_stdin_json_and_bounded(monkeypatch):
    calls = []

    def fake_runner(argv, stdin_text, timeout):
        calls.append((argv, stdin_text, timeout))
        request = json.loads(stdin_text)
        assert request["url"] == "https://example.com/x"
        return 0, json.dumps(
            {
                "source_kind": "social",
                "native_id": "x-1",
                "published_at": "2026-10-02T10:00:00Z",
                "observed_at": "2026-10-02T10:00:01Z",
                "content": "public post",
                "raw": "public post",
                "provider_version": "bridge-1",
                "metadata": {"platform": "x"},
            }
        ), ""

    collector = AgentReachBridgeCollector(
        executable="/opt/senex/agent-reach-bridge",
        runner=fake_runner,
        url_validator=validate_public_url,
    )
    capture = collector.collect("https://example.com/x", timeout=4.0)
    assert capture.provider == "agent_reach"
    assert calls[0][0] == ["/opt/senex/agent-reach-bridge"]
    assert calls[0][2] == 4.0
    assert len(calls) == 1


def test_scrapling_collector_uses_cli_without_shell_and_reads_bounded_output(tmp_path):
    calls = []

    def fake_runner(argv, timeout):
        calls.append((argv, timeout))
        Path(argv[4]).write_text("scraped body", encoding="utf-8")
        return 0, "", ""

    collector = ScraplingCollector(
        executable="/usr/local/bin/scrapling",
        runner=fake_runner,
        url_validator=validate_public_url,
    )
    capture = collector.collect("https://example.com/article", timeout=5.0)
    assert capture.provider == "scrapling"
    assert capture.content == "scraped body"
    assert calls[0][0][0:3] == ["/usr/local/bin/scrapling", "extract", "get"]
    assert "--ai-targeted" in calls[0][0]
    assert "--no-follow-redirects" in calls[0][0]
    assert calls[0][1] == 5.0


def test_patchright_bridge_fails_closed_when_not_configured(monkeypatch):
    monkeypatch.delenv("SENEX_PATCHRIGHT_BRIDGE", raising=False)
    with pytest.raises(BridgeUnavailable):
        PatchrightBridgeCollector.from_env()


def test_decision_paths_do_not_import_external_evidence():
    root = Path(__file__).resolve().parents[1]
    decision_paths = [
        root / "senecio_polymarket" / "backend" / "oracle_runner.py",
        root / "senecio_polymarket" / "oracle" / "predict_only.py",
        root / "senecio_polymarket" / "oracle_runtime" / "predict_only.py",
        root / "senecio_polymarket" / "backend" / "gptrader" / "external_client.py",
        root / "senecio_polymarket" / "backend" / "gptrader" / "task_protocol.py",
    ]
    for path in decision_paths:
        text = path.read_text(encoding="utf-8")
        assert "external_evidence" not in text, path
