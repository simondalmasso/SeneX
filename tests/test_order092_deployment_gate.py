from __future__ import annotations

import json
from pathlib import Path

import pytest

from senecio_polymarket.backend.deployment_gate import (
    CONTRACT,
    DeploymentGateError,
    verify_deployment,
)

CANDIDATE = "a" * 40
MAIN = "b" * 40
TREE = "c" * 40
BUILD = "sha256:" + "d" * 64


def _env(
    *,
    branch: str = "main",
    sha: str = CANDIDATE,
    repo: str = "simondalmasso/SeneX",
) -> dict[str, str]:
    return {
        "NF_DEPLOYMENT_BRANCH": branch,
        "NF_DEPLOYMENT_SHA": sha,
        "NF_DEPLOYMENT_REPO": repo,
    }


def _identity(sha: str = CANDIDATE) -> dict:
    return {
        "exact": True,
        "source_commit": sha,
        "source_tree": TREE,
        "build_digest": BUILD,
    }


def _success_fetch(
    candidate: str = CANDIDATE,
    main_sha: str = MAIN,
    *,
    merge_base: str | None = None,
    ci_runs: list[dict] | None = None,
):
    expected_merge_base = candidate if merge_base is None else merge_base
    successful_runs = (
        [
            {
                "id": 123,
                "head_sha": candidate,
                "head_branch": "main",
                "event": "push",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://example.invalid/run/123",
                "created_at": "2026-09-28T00:00:00Z",
                "updated_at": "2026-09-28T00:10:00Z",
            }
        ]
        if ci_runs is None
        else ci_runs
    )

    def fetch(url: str):
        if "/commits/main" in url:
            return {"sha": main_sha}
        if "/compare/" in url:
            return {"merge_base_commit": {"sha": expected_merge_base}}
        if "/actions/workflows/" in url:
            return {"workflow_runs": successful_runs}
        raise AssertionError(f"unexpected url: {url}")

    return fetch


def test_non_provider_runtime_is_explicitly_not_enforced(tmp_path: Path) -> None:
    result = verify_deployment(
        env={},
        fetch_json=lambda _: (_ for _ in ()).throw(AssertionError("network called")),
        identity_loader=lambda: (_ for _ in ()).throw(AssertionError("identity called")),
        attestation_path=tmp_path / "attestation.json",
    )
    assert result == {
        "contract": CONTRACT,
        "eligible": True,
        "enforced": False,
        "reason": "NOT_PROVIDER_RUNTIME",
    }
    assert not (tmp_path / "attestation.json").exists()


def test_partial_provider_metadata_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(DeploymentGateError, match="PROVIDER_METADATA_INCOMPLETE"):
        verify_deployment(
            env={"NF_DEPLOYMENT_BRANCH": "main"},
            attestation_path=tmp_path / "attestation.json",
        )


@pytest.mark.parametrize(
    ("env", "error"),
    [
        (_env(branch="order092/deployment-gate"), "PROVIDER_BRANCH_DENIED"),
        (_env(repo="other/repo"), "PROVIDER_REPO_DENIED"),
        (_env(sha="short"), "PROVIDER_SHA_INVALID"),
    ],
)
def test_provider_source_metadata_is_strict(
    env: dict[str, str],
    error: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(DeploymentGateError, match=error):
        verify_deployment(env=env, attestation_path=tmp_path / "attestation.json")


def test_provider_sha_must_equal_artifact_identity(tmp_path: Path) -> None:
    with pytest.raises(DeploymentGateError, match="PROVIDER_ARTIFACT_SHA_MISMATCH"):
        verify_deployment(
            env=_env(),
            identity_loader=lambda: _identity("e" * 40),
            fetch_json=_success_fetch(),
            attestation_path=tmp_path / "attestation.json",
        )


def test_candidate_must_be_ancestor_of_current_main(tmp_path: Path) -> None:
    with pytest.raises(DeploymentGateError, match="CANDIDATE_NOT_ANCESTOR_OF_MAIN"):
        verify_deployment(
            env=_env(),
            identity_loader=_identity,
            fetch_json=_success_fetch(merge_base="f" * 40),
            attestation_path=tmp_path / "attestation.json",
        )


def test_exact_sha_requires_green_push_canonical_ci(tmp_path: Path) -> None:
    wrong_run = {
        "id": 7,
        "head_sha": CANDIDATE,
        "head_branch": "main",
        "event": "push",
        "status": "completed",
        "conclusion": "failure",
    }
    with pytest.raises(
        DeploymentGateError,
        match="CANONICAL_CI_EXACT_SHA_NOT_GREEN",
    ):
        verify_deployment(
            env=_env(),
            identity_loader=_identity,
            fetch_json=_success_fetch(ci_runs=[wrong_run]),
            attestation_path=tmp_path / "attestation.json",
        )


def test_verified_provider_deployment_writes_machine_attestation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "deployment_attestation.json"
    result = verify_deployment(
        env=_env(repo="https://github.com/simondalmasso/SeneX.git"),
        identity_loader=_identity,
        fetch_json=_success_fetch(),
        attestation_path=path,
    )
    persisted = json.loads(path.read_text(encoding="utf-8"))

    assert result["contract"] == CONTRACT
    assert result["eligible"] is True
    assert result["enforced"] is True
    assert result["source_commit"] == CANDIDATE
    assert result["main_head"] == MAIN
    assert result["candidate_ancestor_of_main"] is True
    assert result["canonical_ci"]["run_id"] == 123
    assert persisted == result


def test_launcher_invokes_gate_before_any_runtime_process() -> None:
    root = Path(__file__).resolve().parents[1]
    launcher = (
        root / "senecio_polymarket" / "start_single_authority.sh"
    ).read_text(encoding="utf-8")
    gate = "python -m backend.deployment_gate verify"
    assert gate in launcher
    assert "FATAL: deployment gate denied runtime boot" in launcher
    assert launcher.index(gate) < launcher.index("start_reconciler")
