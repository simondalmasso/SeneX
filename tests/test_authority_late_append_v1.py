from __future__ import annotations

import asyncio
import json
import unittest
from unittest import mock

from senecio_polymarket.backend import supabase_client as sc


class _FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code
        self.text = json.dumps(data)

    def json(self):
        return self._data


class AuthorityLateAppendTests(unittest.TestCase):
    def test_id_floor_finds_new_id_with_older_timestamp(self) -> None:
        late = {"id": "2", "ts": "2026-09-13T19:00:00+00:00", "symbol": "BTCUSDT"}
        calls: list[dict[str, str]] = []

        async def fake_get(_client, _path, *, params=None):
            params = dict(params or {})
            calls.append(params)
            return _FakeResponse([late] if params.get("id") == "gt.1" else [])
        with mock.patch.object(sc, "_get_client", return_value=object()), \
             mock.patch.object(sc, "_d1_get", new=fake_get):
            rows = asyncio.run(
                sc._fetch_authority_delta_raw(
                    "BTCUSDT",
                    ("2026-09-13T20:00:00+00:00", "1"),
                    page_size=50,
                    max_pages=2,
                    id_floor=1,
                )
            )

        self.assertEqual([str(row["id"]) for row in rows], ["2"])
        self.assertTrue(any(call.get("id") == "gt.1" for call in calls), calls)


if __name__ == "__main__":
    unittest.main()


class AuthorityLateAppendInvariantTests(unittest.TestCase):
    def test_id_floor_uses_max_integer_primary_key(self) -> None:
        rows = {
            "1": {"id": "1", "ts": "2026-09-13T20:00:00+00:00"},
            "7": {"id": "7", "ts": "2026-09-13T18:00:00+00:00"},
        }
        self.assertEqual(sc._authority_id_floor(rows), 7)

    def test_temporal_cursor_does_not_regress_on_late_append(self) -> None:
        rows = {
            "1": {"id": "1", "ts": "2026-09-13T20:00:00+00:00"},
            "2": {"id": "2", "ts": "2026-09-13T19:00:00+00:00"},
        }
        self.assertEqual(
            sc._authority_temporal_cursor(rows),
            ("2026-09-13T20:00:00+00:00", "1"),
        )
