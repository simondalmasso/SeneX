"""Fail-closed internal artifact identity for SENEX B8.1."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any

CONTRACT = "senex-internal-artifact-identity-v1"
DIGEST_FORMAT = "sha256:path-len+path+byte-len+bytes:v1"
# Runtime root defaults to the container layout (/app). A non-container local
# run may point SENEX_RUNTIME_ROOT at the directory that contains
# backend/ frontend/ oracle/ oracle_runtime/ requirements.lock
# start_single_authority.sh (the /app layout). This selector CANNOT create
# exact=true: exactness still requires the manifest digest to match the
# recomputed digest over canonical inputs at that root (fail-closed).
RUNTIME_ROOT = Path(os.environ.get("SENEX_RUNTIME_ROOT") or "/app")
PROVENANCE_DIRNAME = ".senex-provenance"
IDENTITY_FILENAME = "artifact-identity.json"
CANONICAL_DIRS = ("backend", "frontend", "oracle", "oracle_runtime")
CANONICAL_FILES = ("requirements.lock", "start_single_authority.sh")
MANIFEST_KEYS = frozenset({"contract", "digest_format", "source_commit", "source_tree", "build_digest", "canonical_inputs"})
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_UNAVAILABLE = {"", "unknown", "stale", "none", "null", "unavailable"}


class ArtifactIdentityError(RuntimeError):
    """Artifact identity is missing, malformed, or inconsistent."""


def _normalized(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    return None if text in _UNAVAILABLE else text


def _excluded(relative: str) -> bool:
    path = PurePosixPath(relative)
    if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
        return True
    return len(path.parts) >= 2 and path.parts[0] == "oracle" and path.parts[1] == "senecio_output"


def _assert_no_symlink_chain(root: Path, relative: str) -> Path:
    candidate = root
    for part in PurePosixPath(relative).parts:
        if part in {"", ".", ".."}:
            raise ArtifactIdentityError("CANONICAL_PATH_INVALID")
        candidate = candidate / part
        try:
            mode = candidate.lstat().st_mode
        except OSError as exc:
            raise ArtifactIdentityError(f"CANONICAL_INPUT_UNREADABLE:{relative}") from exc
        if stat.S_ISLNK(mode):
            raise ArtifactIdentityError(f"CANONICAL_SYMLINK_REJECTED:{relative}")
    return candidate


def _read_regular_file(root: Path, relative: str) -> bytes:
    path = _assert_no_symlink_chain(root, relative)
    try:
        root_resolved = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ArtifactIdentityError(f"CANONICAL_INPUT_UNREADABLE:{relative}") from exc
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise ArtifactIdentityError(f"CANONICAL_PATH_ESCAPE:{relative}")
    try:
        mode = path.lstat().st_mode
        if not stat.S_ISREG(mode):
            raise ArtifactIdentityError(f"CANONICAL_INPUT_NOT_REGULAR:{relative}")
        return path.read_bytes()
    except ArtifactIdentityError:
        raise
    except OSError as exc:
        raise ArtifactIdentityError(f"CANONICAL_INPUT_UNREADABLE:{relative}") from exc


def canonical_runtime_files(root: Path | None = None) -> tuple[str, ...]:
    base = Path(root) if root is not None else RUNTIME_ROOT
    if base.is_symlink() or not base.is_dir():
        raise ArtifactIdentityError("RUNTIME_ROOT_INVALID")
    files: list[str] = list(CANONICAL_FILES)
    for dirname in CANONICAL_DIRS:
        directory = _assert_no_symlink_chain(base, dirname)
        if not directory.is_dir():
            raise ArtifactIdentityError(f"CANONICAL_DIR_MISSING:{dirname}")
        for current, dirnames, filenames in os.walk(directory, followlinks=False):
            current_path = Path(current)
            kept_dirs: list[str] = []
            for child in sorted(dirnames):
                child_path = current_path / child
                relative = child_path.relative_to(base).as_posix()
                if _excluded(relative):
                    continue
                if child_path.is_symlink():
                    raise ArtifactIdentityError(f"CANONICAL_SYMLINK_REJECTED:{relative}")
                kept_dirs.append(child)
            dirnames[:] = kept_dirs
            for filename in sorted(filenames):
                path = current_path / filename
                relative = path.relative_to(base).as_posix()
                if _excluded(relative):
                    continue
                if path.is_symlink():
                    raise ArtifactIdentityError(f"CANONICAL_SYMLINK_REJECTED:{relative}")
                files.append(relative)
    ordered = tuple(sorted(files))
    for relative in ordered:
        _read_regular_file(base, relative)
    return ordered


def canonical_build_digest(root: Path | None = None) -> tuple[str, tuple[str, ...]]:
    base = Path(root) if root is not None else RUNTIME_ROOT
    files = canonical_runtime_files(base)
    digest = hashlib.sha256()
    for relative in files:
        path_bytes = relative.encode("utf-8")
        data = _read_regular_file(base, relative)
        digest.update(len(path_bytes).to_bytes(4, "big"))
        digest.update(path_bytes)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return "sha256:" + digest.hexdigest(), files


def _git_object_id(kind: bytes, payload: bytes) -> bytes:
    header = kind + b" " + str(len(payload)).encode("ascii") + b"\0"
    return hashlib.sha1(header + payload).digest()


def _git_tree_id(directory: Path, *, root: bool = False) -> bytes:
    entries: list[tuple[bytes, bool, bytes]] = []
    try:
        children = list(os.scandir(directory))
    except OSError as exc:
        raise ArtifactIdentityError("SOURCE_TREE_UNREADABLE") from exc
    for entry in children:
        if root and entry.name == ".git":
            continue
        name = os.fsencode(entry.name)
        if not name or b"/" in name or b"\0" in name:
            raise ArtifactIdentityError("SOURCE_TREE_NAME_INVALID")
        path = Path(entry.path)
        try:
            mode = path.lstat().st_mode
        except OSError as exc:
            raise ArtifactIdentityError("SOURCE_TREE_UNREADABLE") from exc
        if stat.S_ISDIR(mode):
            oid = _git_tree_id(path)
            mode_text = b"40000"
            is_dir = True
        elif stat.S_ISREG(mode):
            try:
                data = path.read_bytes()
            except OSError as exc:
                raise ArtifactIdentityError("SOURCE_TREE_UNREADABLE") from exc
            oid = _git_object_id(b"blob", data)
            mode_text = b"100755" if (mode & stat.S_IXUSR) else b"100644"
            is_dir = False
        elif stat.S_ISLNK(mode):
            try:
                target = os.fsencode(os.readlink(path))
            except OSError as exc:
                raise ArtifactIdentityError("SOURCE_TREE_UNREADABLE") from exc
            oid = _git_object_id(b"blob", target)
            mode_text = b"120000"
            is_dir = False
        else:
            raise ArtifactIdentityError("SOURCE_TREE_UNSUPPORTED_ENTRY")
        entry_bytes = mode_text + b" " + name + b"\0" + oid
        entries.append((name, is_dir, entry_bytes))
    entries.sort(key=lambda item: item[0] + (b"/" if item[1] else b""))
    payload = b"".join(item[2] for item in entries)
    return _git_object_id(b"tree", payload)


def git_tree_sha(source_root: Path) -> str:
    """Git-compatible tree SHA of a source directory.

    The top-level path may itself be a directory symlink (Windows temp dirs,
    bind mounts). Only the root is resolved; nested entries keep Git's
    blob/tree/symlink object semantics and are never followed as content.
    """
    base = Path(source_root)
    try:
        if not base.exists():
            raise ArtifactIdentityError("SOURCE_ROOT_INVALID")
        resolved = base.resolve(strict=True)
    except OSError as exc:
        raise ArtifactIdentityError("SOURCE_ROOT_INVALID") from exc
    if not resolved.is_dir():
        raise ArtifactIdentityError("SOURCE_ROOT_INVALID")
    return _git_tree_id(resolved, root=True).hex()


def _empty(reason: str) -> dict[str, Any]:
    checks = {
        "manifest_exact": False,
        "commit_exact": False,
        "tree_exact": False,
        "canonical_inputs_exact": False,
        "build_digest_exact": False,
        "build_digest_matches_runtime_files": False,
    }
    return {
        "contract": CONTRACT,
        "digest_format": DIGEST_FORMAT,
        "source_commit": None,
        "source_tree": None,
        "build_digest": None,
        "declared_build_digest": None,
        "computed_build_digest": None,
        "canonical_inputs": [],
        "checks": checks,
        "exact": False,
        "error": reason,
    }


def write_artifact_identity(
    *,
    source_commit: str,
    source_tree: str | None = None,
    source_root: Path | None = None,
    root: Path | None = None,
    output: Path | None = None,
) -> dict[str, Any]:
    base = Path(root) if root is not None else RUNTIME_ROOT
    commit = _normalized(source_commit)
    if source_tree is not None and source_root is not None:
        raise ArtifactIdentityError("SOURCE_TREE_AMBIGUOUS")
    tree = git_tree_sha(Path(source_root)) if source_root is not None else _normalized(source_tree)
    if not commit or not _SHA40.fullmatch(commit):
        raise ArtifactIdentityError("SOURCE_COMMIT_INVALID")
    if not tree or not _SHA40.fullmatch(tree):
        raise ArtifactIdentityError("SOURCE_TREE_INVALID")
    build_digest, canonical_inputs = canonical_build_digest(base)
    destination = Path(output) if output is not None else base / PROVENANCE_DIRNAME / IDENTITY_FILENAME
    try:
        relative_output = destination.resolve(strict=False).relative_to(base.resolve(strict=True)).as_posix()
    except ValueError:
        relative_output = ""
    if relative_output in canonical_inputs or any(
        relative_output == dirname or relative_output.startswith(dirname + "/") for dirname in CANONICAL_DIRS
    ):
        raise ArtifactIdentityError("IDENTITY_OUTPUT_OVERLAPS_CANONICAL_INPUT")
    payload = {
        "contract": CONTRACT,
        "digest_format": DIGEST_FORMAT,
        "source_commit": commit,
        "source_tree": tree,
        "build_digest": build_digest,
        "canonical_inputs": list(canonical_inputs),
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    return payload


def read_artifact_identity(
    identity_file: Path | None = None, *, root: Path | None = None
) -> dict[str, Any]:
    base = Path(root) if root is not None else RUNTIME_ROOT
    path = Path(identity_file) if identity_file is not None else base / PROVENANCE_DIRNAME / IDENTITY_FILENAME
    try:
        if path.is_symlink() or not path.is_file():
            raise ArtifactIdentityError("IDENTITY_FILE_UNAVAILABLE")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return _empty("IDENTITY_FILE_UNAVAILABLE:" + type(exc).__name__)
    if not isinstance(payload, dict):
        return _empty("IDENTITY_MANIFEST_NOT_OBJECT")

    source_commit = _normalized(payload.get("source_commit"))
    source_tree = _normalized(payload.get("source_tree"))
    declared_digest = _normalized(payload.get("build_digest"))
    declared_inputs = payload.get("canonical_inputs")
    manifest_exact = (
        set(payload) == MANIFEST_KEYS
        and payload.get("contract") == CONTRACT
        and payload.get("digest_format") == DIGEST_FORMAT
    )
    commit_exact = bool(source_commit and _SHA40.fullmatch(source_commit))
    tree_exact = bool(source_tree and _SHA40.fullmatch(source_tree))
    digest_exact = bool(declared_digest and _SHA256.fullmatch(declared_digest))
    inputs_shape_exact = isinstance(declared_inputs, list) and all(isinstance(v, str) for v in declared_inputs)

    try:
        computed_digest, current_inputs = canonical_build_digest(base)
    except Exception as exc:
        result = _empty("BUILD_DIGEST_RECOMPUTE_FAILED:" + type(exc).__name__)
        result["source_commit"] = source_commit
        result["source_tree"] = source_tree
        result["build_digest"] = declared_digest
        result["declared_build_digest"] = declared_digest
        result["computed_build_digest"] = None
        return result

    canonical_inputs_exact = inputs_shape_exact and tuple(declared_inputs) == current_inputs
    structural_exact = manifest_exact and commit_exact and tree_exact and digest_exact and canonical_inputs_exact
    digest_matches = bool(structural_exact and declared_digest == computed_digest)
    checks = {
        "manifest_exact": manifest_exact,
        "commit_exact": commit_exact,
        "tree_exact": tree_exact,
        "canonical_inputs_exact": canonical_inputs_exact,
        "build_digest_exact": digest_exact,
        "build_digest_matches_runtime_files": digest_matches,
    }
    exact = all(checks.values())
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "contract": CONTRACT,
        "digest_format": DIGEST_FORMAT,
        "source_commit": source_commit,
        "source_tree": source_tree,
        "build_digest": declared_digest,
        "declared_build_digest": declared_digest,
        "computed_build_digest": computed_digest,
        "canonical_inputs": list(current_inputs),
        "checks": checks,
        "exact": exact,
        "error": None if exact else "IDENTITY_VALIDATION_FAILED:" + ",".join(failed),
    }


def require_artifact_identity(identity_file: Path | None = None, *, root: Path | None = None) -> dict[str, Any]:
    identity = read_artifact_identity(identity_file, root=root)
    if not identity["exact"]:
        raise ArtifactIdentityError(str(identity.get("error") or "ARTIFACT_IDENTITY_NOT_EXACT"))
    return identity


def internal_identity_projection(
    identity_file: Path | None = None, *, root: Path | None = None
) -> dict[str, str]:
    """Canonical authority/seal identity. Never includes OCI/runtime environment data."""
    identity = require_artifact_identity(identity_file, root=root)
    return {
        "contract": str(identity["contract"]),
        "source_commit": str(identity["source_commit"]),
        "source_tree": str(identity["source_tree"]),
        "build_digest": str(identity["build_digest"]),
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Create SENEX B8.1 internal artifact identity")
    parser.add_argument("command", choices=("write",))
    parser.add_argument("--root", default=str(RUNTIME_ROOT))
    parser.add_argument("--output", default=None)
    parser.add_argument("--source-commit", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-tree")
    source.add_argument("--source-root")
    args = parser.parse_args()
    payload = write_artifact_identity(
        source_commit=args.source_commit,
        source_tree=args.source_tree,
        source_root=Path(args.source_root) if args.source_root else None,
        root=Path(args.root),
        output=Path(args.output) if args.output else None,
    )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
