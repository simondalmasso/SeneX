"""CI-discovered contract tests for the regenerated Fase 1 audit harness."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AUDIT_DIR = ROOT / "audit" / "fase1_harness_v2_20261019"
sys.path.insert(0, str(AUDIT_DIR))

import fase1_harness as harness  # noqa: E402


EXPECTED_COMMIT = "c" * 40
EXPECTED_TREE = "a" * 40
EXPECTED_BUILD = "sha256:" + "b" * 64


def runtime_stub():
    return {
        "SEAL_CONTRACT": "contract",
        "_row_key": lambda row: (row["ts"], row["id"]),
        "_sha": lambda value: "hash",
        "_cursor_for_rows": lambda rows: {"id": rows[-1]["id"]},
    }


def valid_payload():
    return {
        "contract": "contract",
        "rows": [{"id": 1, "ts": "2026-01-01"}],
        "row_count": 1,
        "rows_hash": "hash",
        "cursor": {"id": 1},
        "seal_hash": "hash",
        "source_commit": EXPECTED_COMMIT,
        "source_tree": EXPECTED_TREE,
        "build_digest": EXPECTED_BUILD,
    }


def check_values(checks):
    return {name: value for name, value, _ in checks}


def test_complete_matching_producer_identity_is_accepted():
    ok, checks = harness.verify_seal(
        valid_payload(),
        runtime_stub(),
        EXPECTED_COMMIT,
        EXPECTED_TREE,
        EXPECTED_BUILD,
    )
    assert ok is True
    assert all(value is True for _, value, _ in checks)


def test_missing_identity_expectations_fail_closed():
    ok, checks = harness.verify_seal(valid_payload(), runtime_stub(), None, None, None)
    values = check_values(checks)
    assert ok is False
    assert values["C7a source_commit"] is None
    assert values["C7b source_tree"] is None
    assert values["C7c build_digest"] is None


def test_missing_tree_expectation_fails_closed():
    ok, checks = harness.verify_seal(
        valid_payload(), runtime_stub(), EXPECTED_COMMIT, None, EXPECTED_BUILD
    )
    assert ok is False
    assert check_values(checks)["C7b source_tree"] is None


def test_missing_build_expectation_fails_closed():
    ok, checks = harness.verify_seal(
        valid_payload(), runtime_stub(), EXPECTED_COMMIT, EXPECTED_TREE, None
    )
    assert ok is False
    assert check_values(checks)["C7c build_digest"] is None


def test_absent_or_mismatched_tree_and_build_fail_closed():
    payload = valid_payload()
    payload.pop("source_tree")
    ok, checks = harness.verify_seal(
        payload, runtime_stub(), EXPECTED_COMMIT, EXPECTED_TREE, EXPECTED_BUILD
    )
    assert ok is False
    assert check_values(checks)["C7b source_tree"] is False

    payload = valid_payload()
    payload.pop("build_digest")
    ok, checks = harness.verify_seal(
        payload, runtime_stub(), EXPECTED_COMMIT, EXPECTED_TREE, EXPECTED_BUILD
    )
    assert ok is False
    assert check_values(checks)["C7c build_digest"] is False

    payload = valid_payload()
    payload["source_tree"] = "d" * 40
    payload["build_digest"] = "sha256:" + "e" * 64
    ok, checks = harness.verify_seal(
        payload, runtime_stub(), EXPECTED_COMMIT, EXPECTED_TREE, EXPECTED_BUILD
    )
    assert ok is False
    values = check_values(checks)
    assert values["C7b source_tree"] is False
    assert values["C7c build_digest"] is False


def test_load_rows_rejects_incomplete_identity_before_returning_rows(tmp_path, capsys):
    import json

    evidence = tmp_path / "authority-BTCUSDT.json"
    evidence.write_text(json.dumps(valid_payload()), encoding="utf-8")
    loaded = harness.load_rows(str(evidence), runtime_stub(), None, EXPECTED_TREE, EXPECTED_BUILD)
    output = capsys.readouterr().out
    assert loaded is None
    assert "IDENTITY_COMPLETE=NO" in output
    assert "CHECKS_APPLICABLE=FAIL" in output
    assert "BLOQUEO:" in output


def test_unsealed_exports_cannot_produce_accepted_rows(tmp_path, capsys):
    for name, suffix, body in (
        ("predictions", ".jsonl", '{"id": 1, "outcome": "WIN"}\\n'),
        ("predictions", ".csv", "id,outcome\\n1,WIN\\n"),
    ):
        path = tmp_path / f"{name}{suffix}"
        path.write_text(body, encoding="utf-8")
        loaded = harness.load_rows(
            str(path), runtime_stub(), EXPECTED_COMMIT, EXPECTED_TREE, EXPECTED_BUILD
        )
        assert loaded is None
    output = capsys.readouterr().out
    assert output.count("BLOQUEO:") == 2
