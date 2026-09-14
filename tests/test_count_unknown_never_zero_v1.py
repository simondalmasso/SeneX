from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from senecio_polymarket.backend import supabase_client as sc


class CountUnknownNeverZeroTests(unittest.TestCase):
    def test_soft_count_returns_none_when_exact_count_unavailable(self) -> None:
        with mock.patch.object(
            sc,
            "count_predictions_exact",
            new=mock.AsyncMock(side_effect=sc.ExactCountUnavailableError("D1_DOWN")),
        ):
            result = asyncio.run(sc.count_predictions())
        self.assertIsNone(result)

    def test_soft_count_preserves_legitimate_zero(self) -> None:
        with mock.patch.object(
            sc, "count_predictions_exact", new=mock.AsyncMock(return_value=0)
        ):
            result = asyncio.run(sc.count_predictions())
        self.assertEqual(result, 0)

    def test_soft_count_preserves_positive_exact_count(self) -> None:
        with mock.patch.object(
            sc, "count_predictions_exact", new=mock.AsyncMock(return_value=4410)
        ):
            result = asyncio.run(sc.count_predictions())
        self.assertEqual(result, 4410)


if __name__ == "__main__":
    unittest.main()
