from __future__ import annotations

import json
from urllib.parse import urlsplit

import pytest

from ops.opensre.h011_readonly_probe import DEFAULT_BASE_URL, PROBES, read_snapshot, validate_base_url


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self.status = status
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return self.body


class FakeOpener:
    def __init__(self, payloads: dict[str, dict], statuses: dict[str, int] | None = None):
        self.payloads = payloads
        self.statuses = statuses or {}
        self.calls = []

    def open(self, request, timeout=8.0):
        self.calls.append((request.get_method(), request.full_url, timeout))
        path = urlsplit(request.full_url).path
        name = next((name for name, route in PROBES if urlsplit(DEFAULT_BASE_URL + route).path == path), None)
        if name is None:
            raise AssertionError(f"unexpected URL: {request.full_url}")
        return FakeResponse(self.payloads[name], self.statuses.get(name, 200))


def sample_payloads():
    return {
        "healthz": {"alive": True, "status": "ok"},
        "readyz": {"status": "ready", "paper_control_ready": True, "paper_lock": True, "orders_disabled": True, "provenance_exact": True},
        "provenance": {"exact": True, "source_commit": "a" * 40, "source_tree": "b" * 40},
        "oracle_score": {"score_status": "REJECTED", "safety": {"trade_mode": "PAPER", "orders_enabled": False, "live_capital_locked": True, "hard_paper_lock": True}},
        "paper_live_gate": {"orders_enabled": False, "live_capital_locked": True, "hard_paper_lock": True, "trade_mode": "PAPER"},
        "openapi": {"paths": {"/healthz": {"get": {}}, "/api/portfolio/live_gate": {"get": {}}}},
    }


def test_only_allowlisted_https_host_is_accepted():
    assert validate_base_url(DEFAULT_BASE_URL) == DEFAULT_BASE_URL
    for url in ("http://h011-web--senecio-h011--wbjggn89fnf8.code.run", "https://example.com", DEFAULT_BASE_URL + "/api", DEFAULT_BASE_URL + "?debug=1", DEFAULT_BASE_URL + "#fragment"):
        with pytest.raises(ValueError):
            validate_base_url(url)


def test_snapshot_is_get_only_and_checks_paper_safety():
    opener = FakeOpener(sample_payloads())
    report = read_snapshot(opener=opener)
    assert report["mode"] == "READ_ONLY"
    assert report["status"] == "HEALTHY"
    assert all(method == "GET" for method, _url, _timeout in opener.calls)
    assert len(opener.calls) == len(PROBES)
    assert report["security"]["account_endpoints_called"] == 0
    assert report["security"]["order_endpoints_called"] == 0
    assert report["checks"]["public_api_read_only"] is True


def test_non_get_openapi_surface_is_reported_as_not_read_only():
    payloads = sample_payloads()
    payloads["openapi"]["paths"]["/unsafe"] = {"post": {}}
    report = read_snapshot(opener=FakeOpener(payloads))
    assert report["status"] == "DEGRADED_OR_INCOMPLETE"
    assert report["checks"]["public_api_read_only"] is False
    assert report["probes"]["openapi"]["summary"]["non_read_only_operations"] == ["POST /unsafe"]


def test_incomplete_probe_fails_closed():
    report = read_snapshot(opener=FakeOpener(sample_payloads(), {"readyz": 503}))
    assert report["status"] == "DEGRADED_OR_INCOMPLETE"
    assert report["probes"]["readyz"]["http_status"] == 503
