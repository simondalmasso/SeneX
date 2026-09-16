from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQ = ROOT / "senecio_polymarket" / "requirements.txt"
LOCK = ROOT / "senecio_polymarket" / "requirements.lock"
EXPECTED_ROOTS = {
    "ccxt",
    "fastapi",
    "httpx",
    "numpy",
    "pydantic",
    "python-dotenv",
    "uvicorn",
    "websockets",
}
FORBIDDEN_ROOTS = {
    "shap",
    "prometheus-client",
    "pydantic-settings",
    "sse-starlette",
}
STANDARD_UVICORN_LOCK_DEPS = {"httptools", "pyyaml", "uvloop", "watchfiles"}


def _requirement_names(path: Path) -> set[str]:
    names = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("--hash="):
            continue
        name = line.split("==", 1)[0].strip().lower().split("[", 1)[0]
        names.add(name)
    return names


def test_direct_dependency_manifest_is_explicit_and_minimal():
    assert REQ.exists(), "requirements.txt must be restored as the source of requirements.lock"
    roots = _requirement_names(REQ)
    assert roots == EXPECTED_ROOTS
    assert roots.isdisjoint(FORBIDDEN_ROOTS)
    assert "uvicorn[standard]==0.44.0" in REQ.read_text(encoding="utf-8").splitlines()


def test_lock_preserves_uvicorn_standard_linux_runtime():
    locked = _requirement_names(LOCK)
    assert STANDARD_UVICORN_LOCK_DEPS <= locked
