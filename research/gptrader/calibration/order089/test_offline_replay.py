from __future__ import annotations

import unittest

from offline_replay import ReplayError, evaluate


class OfflineReplayTests(unittest.TestCase):
    def packet(self, packet_id: str, ts: str, packet_hash: str = "h1"):
        return {
            "packet_id": packet_id,
            "packet_hash": packet_hash,
            "timestamp": ts,
            "symbol": "BTCUSDT",
        }

    def decisions(self, packet_id: str, packet_hash: str = "h1"):
        return [
            {
                "packet_id": packet_id,
                "packet_hash": packet_hash,
                "arm": arm,
                "action": action,
                "decision_frozen_at": "2026-09-28T01:00:00Z",
            }
            for arm, action in (
                ("CONTROL", "TAKE"),
                ("DECISION_AGENT", "TAKE"),
                ("CALIBRATED_AGENT", "ABSTAIN"),
            )
        ]

    def test_same_packet_hash_all_arms_and_clustered_summary(self):
        packets = [self.packet("p1", "2026-09-28T00:15:00Z")]
        settlements = [
            {
                "packet_id": "p1",
                "senex_direction_correct": True,
                "settled_at": "2026-09-28T02:00:00Z",
            }
        ]
        result = evaluate(packets, self.decisions("p1"), settlements)
        self.assertEqual(result["independent_1h_clusters"], 1)
        self.assertEqual(result["arms"]["CONTROL"]["take_count"], 1)
        self.assertEqual(result["arms"]["CALIBRATED_AGENT"]["take_count"], 0)

    def test_settlement_before_all_frozen_decisions_fails(self):
        packets = [self.packet("p1", "2026-09-28T00:15:00Z")]
        decisions = self.decisions("p1")[:-1]
        settlements = [
            {
                "packet_id": "p1",
                "senex_direction_correct": True,
                "settled_at": "2026-09-28T02:00:00Z",
            }
        ]
        with self.assertRaises(ReplayError):
            evaluate(packets, decisions, settlements)

    def test_incident_packet_is_preserved_but_excluded(self):
        packets = [self.packet("p1", "2026-09-27T21:40:00Z")]
        settlements = [
            {
                "packet_id": "p1",
                "senex_direction_correct": False,
                "settled_at": "2026-09-28T02:00:00Z",
            }
        ]
        result = evaluate(packets, self.decisions("p1"), settlements)
        self.assertEqual(result["eligible_packets"], 0)
        self.assertEqual(result["quarantined_packets_excluded"], 1)

    def test_packet_hash_mismatch_fails(self):
        packets = [self.packet("p1", "2026-09-28T00:15:00Z")]
        decisions = self.decisions("p1")
        decisions[0]["packet_hash"] = "wrong"
        with self.assertRaises(ReplayError):
            evaluate(packets, decisions, [])


if __name__ == "__main__":
    unittest.main()
