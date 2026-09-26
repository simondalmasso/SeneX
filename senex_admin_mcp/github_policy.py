from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

OWNER = "simondalmasso"
REPO = "SeneX"
BRANCH = "main"
REQUIRED_CHECK = "canonical-ci"
GITHUB_API = "https://api.github.com"
API_VERSION = "2026-03-10"
ALLOWED_LOGIN = "simondalmasso"


@dataclass(frozen=True)
class GitHubApiError(RuntimeError):
    status: int
    detail: str

    def __str__(self) -> str:
        return f"GitHub API {self.status}: {self.detail}"


def branch_protection_payload() -> dict[str, Any]:
    """Return the only protection policy this service is allowed to write."""
    return {
        "required_status_checks": {
            "strict": True,
            "contexts": [REQUIRED_CHECK],
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": False,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        "required_linear_history": False,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        "required_conversation_resolution": False,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


def _api_request(
    token: str,
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Only GitHub App user-to-server access tokens are accepted. This rejects
    # PATs and installation tokens so the action is tied to the authenticated
    # human plus the GitHub App's narrow repository permissions.
    if not token.startswith("ghu_"):
        raise ValueError("GITHUB_APP_USER_TOKEN_REQUIRED")

    data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
    request = Request(
        f"{GITHUB_API}{path}",
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "senex-github-admin-mcp/1",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=15) as response:
            raw = response.read()
    except HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
            detail = str(payload.get("message") or "request failed")
        except Exception:
            detail = "request failed"
        raise GitHubApiError(exc.code, detail) from None
    except URLError as exc:
        raise GitHubApiError(0, f"network error: {type(exc.reason).__name__}") from None

    return json.loads(raw.decode("utf-8")) if raw else {}


def require_authorized_user(token: str) -> dict[str, Any]:
    """Validate the GitHub SSO identity before any repository operation."""
    user = _api_request(token, "/user")
    if str(user.get("login") or "").lower() != ALLOWED_LOGIN.lower():
        raise PermissionError("SENEX_ADMIN_USER_NOT_ALLOWED")
    return {"login": user["login"], "id": user.get("id")}


def get_protection(token: str) -> dict[str, Any]:
    require_authorized_user(token)
    return _api_request(token, f"/repos/{OWNER}/{REPO}/branches/{BRANCH}/protection")


def apply_protection(token: str) -> dict[str, Any]:
    require_authorized_user(token)
    return _api_request(
        token,
        f"/repos/{OWNER}/{REPO}/branches/{BRANCH}/protection",
        method="PUT",
        body=branch_protection_payload(),
    )


def protection_summary(payload: dict[str, Any]) -> dict[str, Any]:
    checks = payload.get("required_status_checks") or {}
    pr = payload.get("required_pull_request_reviews")
    enforce_admins = payload.get("enforce_admins")
    force = payload.get("allow_force_pushes")
    deletions = payload.get("allow_deletions")

    def enabled(value: Any) -> bool:
        if isinstance(value, dict):
            return bool(value.get("enabled"))
        return bool(value)

    contexts = list(checks.get("contexts") or [])
    summary = {
        "repo": f"{OWNER}/{REPO}",
        "branch": BRANCH,
        "strict_status_checks": bool(checks.get("strict")),
        "canonical_ci_required": REQUIRED_CHECK in contexts,
        "pull_request_required": isinstance(pr, dict),
        "required_approvals": (
            pr.get("required_approving_review_count") if isinstance(pr, dict) else None
        ),
        "admins_enforced": enabled(enforce_admins),
        "force_push_blocked": not enabled(force),
        "deletion_blocked": not enabled(deletions),
    }
    summary["pass"] = all(
        (
            summary["strict_status_checks"],
            summary["canonical_ci_required"],
            summary["pull_request_required"],
            summary["required_approvals"] == 0,
            summary["admins_enforced"],
            summary["force_push_blocked"],
            summary["deletion_blocked"],
        )
    )
    return summary
