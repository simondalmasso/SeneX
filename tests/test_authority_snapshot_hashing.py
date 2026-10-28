from __future__ import annotations

import hashlib

import pytest

from senecio_polymarket.backend.authority_snapshot import (
    _canonical_json,
    _canonical_sha256,
)


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"z": 1, "a": [True, None, 3.5]},
        {"nested": {"b": "café", "a": "line\nfeed"}, "rows": [{"id": 2}, {"id": 1}]},
        {"fallback": object()},
    ],
)
def test_incremental_canonical_hash_matches_existing_json_bytes(value: object) -> None:
    expected = hashlib.sha256(_canonical_json(value)).hexdigest()
    assert _canonical_sha256(value) == expected
