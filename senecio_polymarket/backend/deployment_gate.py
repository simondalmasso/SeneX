"""Machine-enforced deployment eligibility gate for SENEX H011.

The gate runs before any runtime process starts. It activates automatically on
Northflank when NF_DEPLOYMENT_* managed variables are present, and it is skipped
for non-provider local/CI runs where all of those variables are absent.

A provider deployment is eligible only when:
  * Northflank says the source branch is protected main;
  * provider SHA equals the immutable artifact-identity source commit;
  * that commit is an ancestor of or equal to current GitHub main;
  * canonical CI succeeded for that exact commit as a push on main.

Missing, malformed, stale, or unverifiable evidence fails closed.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .artifact_identity import require_artifact_identity

CONTRACT = "senex-deployment-attestation-v1"
EXPECTED_REPO = "simondalmasso/SeneX"
EXPECTED_BRANCH = "main"
CANONICAL_WORKFLOW = "senex-current-ci.yml"
GITHUB_API = "https://api.github.com"
DEFAULT_ATTESTATION_PATH = Path("/app/polymarket/results/deployment_attestation.json")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")

_PROVIDER_KEYS = (
    "NF_DEPLOYMENT_BRANCH",
    "NF_DEPLOYMENT_SHA",
    "NF_DEPLOYMENT_REPO",
)


class DeploymentGateError(RuntimeError):
    """Deployment eligibility cannot be proven."""


def _normalized_repo(value: str) -> str:
    text = str(value or "").strip()
    if text.endswith(".git"):
        text = text[:-4]
    for prefix in (
        "https://github.com/",
        "http://github.com/",
        "github.com/",
        "git@github.com:",
    ):
        if text.lower().startswith(prefix):
            text = text[len(prefix):]
            break
    return text.strip("/")


def _fetch_json(url: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "senex-deployment-gate/1",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=8.0) as response:
            if int(getattr(response, "status", 200)) != 200:
                raise DeploymentGateError(
                    f"GITHUB_HTTP_STATUS:{getattr(response, 'status', 'UNKNOWN')}"
                )
            payload = json.loads(response.read().decode("utf-8"))
    except DeploymentGateError:
        raise
    except Exception as exc:
        raise DeploymentGateError(
            f"GITHUB_UNAVAILABLE:{type(exc).__name__}"
        ) from exc
    return payload


def _provider_deployment(env: Mapping[str, str]) -> dict[str, str] | None:
    raw = {key: str(env.get(key) or "").strip() for key in _PROVIDER_KEYS}
    present = {key: bool(value) for key, value in raw.items()}
    if not any(present.values()):
        return None
    if not all(present.values()):
        missing = ",".join(sorted(key for key, ok in present.items() if not ok))
        raise DeploymentGateError(f"PROVIDER_METADATA_INCOMPLETE:{missing}")

    branch = raw["NF_DEPLOYMENT_BRANCH"]
    sha = raw["NF_DEPLOYMENT_SHA"].lower()
    repo = _normalized_repo(raw["NF_DEPLOYMENT_REPO"])

    if branch != EXPECTED_BRANCH:
        raise DeploymentGateError(f"PROVIDER_BRANCH_DENIED:{branch}")
    if not _SHA40.fullmatch(sha):
        raise DeploymentGateError("PROVIDER_SHA_INVALID")
    if repo.lower() != EXPECTED_REPO.lower():
        raise DeploymentGateError(f"PROVIDER_REPO_DENIED:{repo}")

    return {"branch": branch, "sha": sha, "repo": repo}


def _canonical_ci_success(
    candidate_sha: str,
    fetch_json: Callable[[str], Any],
) -> dict[str, Any]:
    query = urllib.parse.urlencode(
        {
            "branch": EXPECTED_BRANCH,
            "head_sha": candidate_sha,
            "status": "success",
            "event": "push",
            "per_page": "20",
        }
    )
    url = (
        f"{GITHUB_API}/repos/{EXPECTED_REPO}/actions/workflows/"
        f"{CANONICAL_WORKFLOW}/runs?{query}"
    )
    payload = fetch_json(url)
    runs = payload.get("workflow_runs") if isinstance(payload, dict) else None
    if not isinstance(runs, list):
        raise DeploymentGateError("CANONICAL_CI_RESPONSE_INVALID")
    for run in runs:
        if not isinstance(run, dict):
            continue
        if (
            str(run.get("head_sha") or "").lower() == candidate_sha
            and str(run.get("head_branch") or "") == EXPECTED_BRANCH
            and str(run.get("event") or "") == "push"
            and str(run.get("status") or "") == "completed"
            and str(run.get("conclusion") or "") == "success"
        ):
            return {
                "run_id": run.get("id"),
                "html_url": run.get("html_url"),
                "created_at": run.get("created_at"),
                "updated_at": run.get("updated_at"),
            }
    raise DeploymentGateError("CANONICAL_CI_EXACT_SHA_NOT_GREEN")


def _write_attestation(path: Path, payload: dict[str, Any]) -> None:
    parent = path.parent
    if not parent.is_dir():
        raise DeploymentGateError("ATTESTATION_PARENT_UNAVAILABLE")
    data = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            prefix=".deployment-attestation.",
            suffix=".tmp",
            dir=str(parent),
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
        if hasattr(os, "O_DIRECTORY"):
            dir_fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
    except DeploymentGateError:
        raise
    except Exception as exc:
        raise DeploymentGateError(
            f"ATTESTATION_PERSIST_FAILED:{type(exc).__name__}"
        ) from exc
    finally:
        if tmp_name:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def verify_deployment(
    *,
    env: Mapping[str, str] | None = None,
    fetch_json: Callable[[str], Any] = _fetch_json,
    identity_loader: Callable[[], dict[str, Any]] = require_artifact_identity,
    attestation_path: Path = DEFAULT_ATTESTATION_PATH,
) -> dict[str, Any]:
    environment = os.environ if env is None else env
    provider = _provider_deployment(environment)
    if provider is None:
        return {
            "contract": CONTRACT,
            "eligible": True,
            "enforced": False,
            "reason": "NOT_PROVIDER_RUNTIME",
        }

    identity = identity_loader()
    if not isinstance(identity, dict) or identity.get("exact") is not True:
        raise DeploymentGateError("ARTIFACT_IDENTITY_NOT_EXACT")
    source_commit = str(identity.get("source_commit") or "").lower()
    if source_commit != provider["sha"]:
        raise DeploymentGateError("PROVIDER_ARTIFACT_SHA_MISMATCH")

    main_payload = fetch_json(
        f"{GITHUB_API}/repos/{EXPECTED_REPO}/commits/{EXPECTED_BRANCH}"
    )
    if not isinstance(main_payload, dict):
        raise DeploymentGateError("MAIN_HEAD_RESPONSE_INVALID")
    main_sha = str(main_payload.get("sha") or "").lower()
    if not _SHA40.fullmatch(main_sha):
        raise DeploymentGateError("MAIN_HEAD_SHA_INVALID")

    compare_payload = fetch_json(
        f"{GITHUB_API}/repos/{EXPECTED_REPO}/compare/{source_commit}...{main_sha}"
    )
    merge_base = (
        compare_payload.get("merge_base_commit")
        if isinstance(compare_payload, dict)
        else None
    )
    merge_base_sha = (
        str(merge_base.get("sha") or "").lower()
        if isinstance(merge_base, dict)
        else ""
    )
    if merge_base_sha != source_commit:
        raise DeploymentGateError("CANDIDATE_NOT_ANCESTOR_OF_MAIN")

    ci = _canonical_ci_success(source_commit, fetch_json)
    attestation = {
        "contract": CONTRACT,
        "eligible": True,
        "enforced": True,
        "repo": EXPECTED_REPO,
        "branch": EXPECTED_BRANCH,
        "provider_sha": provider["sha"],
        "source_commit": source_commit,
        "source_tree": identity.get("source_tree"),
        "build_digest": identity.get("build_digest"),
        "main_head": main_sha,
        "candidate_ancestor_of_main": True,
        "canonical_ci": ci,
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_attestation(Path(attestation_path), attestation)
    return attestation


def _main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] != "verify":
        print("usage: python -m backend.deployment_gate verify", file=sys.stderr)
        return 2
    try:
        result = verify_deployment()
    except Exception as exc:
        print(
            f"DEPLOYMENT_GATE_DENY:{type(exc).__name__}:{exc}",
            file=sys.stderr,
        )
        return 78
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
