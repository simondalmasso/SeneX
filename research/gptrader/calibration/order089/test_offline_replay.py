from __future__ import annotations

import copy
import unittest

import offline_replay
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


class OfflineHardeningAdversarialTests(unittest.TestCase):
    def packet(self, packet_id, ts, *, prediction_id=None, source_sha="c7", exact=True):
        row = {
            "packet_id": packet_id,
            "packet_hash": f"hash-{packet_id}",
            "timestamp": ts,
            "symbol": "BTCUSDT",
            "source_sha": source_sha,
            "provenance_exact": exact,
        }
        if prediction_id is not None:
            row["prediction_id"] = prediction_id
        return row

    def test_clean_baseline_requires_exact_restored_provenance(self):
        fn = getattr(offline_replay, "resolve_clean_baseline", None)
        self.assertTrue(callable(fn), "resolve_clean_baseline must exist")
        missing = self.packet("p1", "2026-09-27T22:10:23Z")
        missing.pop("source_sha")
        self.assertIsNone(fn([missing], expected_source_sha="c7"))
        mismatch = self.packet("p2", "2026-09-27T22:10:23Z", source_sha="wrong")
        self.assertIsNone(fn([mismatch], expected_source_sha="c7"))
        good = self.packet("p3", "2026-09-27T22:10:23Z")
        self.assertEqual(fn([good], expected_source_sha="c7"), "2026-09-27T22:10:23Z")

    def test_epoch_overlap_rejected_by_packet_or_hour_cluster(self):
        fn = getattr(offline_replay, "validate_epoch_partition", None)
        self.assertTrue(callable(fn), "validate_epoch_partition must exist")
        train = [self.packet("p1", "2026-09-28T00:05:00Z")]
        calibration_same_packet = [self.packet("p1", "2026-09-28T01:05:00Z")]
        with self.assertRaises(ReplayError):
            fn(train, calibration_same_packet, [])
        calibration_same_cluster = [self.packet("p2", "2026-09-28T00:55:00Z")]
        with self.assertRaises(ReplayError):
            fn(train, calibration_same_cluster, [])

    def test_quarantine_exact_boundaries_and_ids_independent_of_timestamp(self):
        q = offline_replay._quarantined
        self.assertFalse(q(self.packet("a", "2026-09-27T21:29:52.999999Z")))
        self.assertTrue(q(self.packet("b", "2026-09-27T21:29:53Z")))
        self.assertTrue(q(self.packet("c", "2026-09-27T22:10:22.999999Z")))
        self.assertFalse(q(self.packet("d", "2026-09-27T22:10:23Z")))
        for prediction_id in ("6868", "6870", "6872"):
            self.assertTrue(
                q(self.packet(f"id-{prediction_id}", "2026-10-10T00:00:00Z", prediction_id=prediction_id))
            )

    def test_calibration_id_ignores_agent_metadata_but_changes_on_core_mutation(self):
        fn = getattr(offline_replay, "compute_calibration_id", None)
        self.assertTrue(callable(fn), "compute_calibration_id must exist")
        artifact = {
            "schema_version": "senex.calibration.order089.v1",
            "decision_protocol_version": "decision.v1",
            "source_epochs": {"train_epoch_hash": "a" * 64},
            "windows": {"train_start": "2026-09-28T00:00:00Z"},
            "parameters": {"candidate_name": "CONF_Q70"},
            "frozen": {"direction_owner": "SENEX"},
            "objective": {"primary": "DEPENDENCE_AWARE_INCREMENTAL_DIRECTIONAL_UTILITY"},
            "constraints": ["NO_LOOKAHEAD"],
            "code_sha": "b" * 40,
            "random_seed": None,
            "evaluation_provenance": [{"agent_id": "agent-a", "model_name": "model-a"}],
        }
        base = fn(artifact)
        metadata_only = copy.deepcopy(artifact)
        metadata_only["evaluation_provenance"] = [{"agent_id": "agent-b", "model_name": "model-b"}]
        self.assertEqual(fn(metadata_only), base)
        for section, key, value in (
            ("parameters", "candidate_name", "CONF_Q80"),
            ("windows", "train_start", "2026-09-29T00:00:00Z"),
            (None, "code_sha", "c" * 40),
            (None, "decision_protocol_version", "decision.v2"),
        ):
            changed = copy.deepcopy(artifact)
            if section is None:
                changed[key] = value
            else:
                changed[section][key] = value
            self.assertNotEqual(fn(changed), base)

    def test_trivial_abstain_and_zero_uncalibrated_take_rate_are_not_promotable(self):
        fn = getattr(offline_replay, "coverage_guard_passes", None)
        self.assertTrue(callable(fn), "coverage_guard_passes must exist")
        self.assertFalse(fn(candidate_take_count=0, uncalibrated_take_count=10))
        self.assertFalse(fn(candidate_take_count=1, uncalibrated_take_count=3))
        self.assertTrue(fn(candidate_take_count=2, uncalibrated_take_count=3))
        self.assertFalse(fn(candidate_take_count=0, uncalibrated_take_count=0))

    def test_agent_replacement_preserves_protocol_artifact_but_resets_evidence(self):
        fn = getattr(offline_replay, "replacement_shadow_state", None)
        self.assertTrue(callable(fn), "replacement_shadow_state must exist")
        state = fn(
            calibration_id="cal089_" + "a" * 64,
            decision_protocol_version="decision.v1",
            new_provenance={"agent_id": "agent-new", "model_name": "model-new"},
        )
        self.assertEqual(state["calibration_id"], "cal089_" + "a" * 64)
        self.assertEqual(state["decision_protocol_version"], "decision.v1")
        self.assertEqual(state["stage"], "SHADOW_PAPER")
        self.assertEqual(state["independent_1h_clusters"], 0)
        self.assertEqual(state["performance_evidence"], [])

    def test_settlement_must_follow_every_arm_freeze(self):
        packets = [self.packet("p1", "2026-09-28T00:15:00Z")]
        decisions = [
            {
                "packet_id": "p1",
                "packet_hash": "hash-p1",
                "arm": arm,
                "action": "TAKE",
                "decision_frozen_at": freeze,
            }
            for arm, freeze in (
                ("CONTROL", "2026-09-28T01:00:00Z"),
                ("DECISION_AGENT", "2026-09-28T01:00:00Z"),
                ("CALIBRATED_AGENT", "2026-09-28T03:00:00Z"),
            )
        ]
        settlements = [{
            "packet_id": "p1",
            "senex_direction_correct": True,
            "settled_at": "2026-09-28T02:00:00Z",
        }]
        with self.assertRaises(ReplayError):
            evaluate(packets, decisions, settlements)

    def test_geometry_cannot_bypass_calendar_day_minimums(self):
        fn = getattr(offline_replay, "evidence_gate_passes", None)
        self.assertTrue(callable(fn), "evidence_gate_passes must exist")
        self.assertFalse(fn("TRAIN", independent_1h_clusters=168, calendar_days=6))
        self.assertTrue(fn("TRAIN", independent_1h_clusters=168, calendar_days=7))
        self.assertFalse(fn("CALIBRATION", independent_1h_clusters=168, calendar_days=6))
        self.assertTrue(fn("CALIBRATION", independent_1h_clusters=168, calendar_days=7))
        self.assertFalse(fn("HOLDOUT", independent_1h_clusters=600, calendar_days=24))
        self.assertTrue(fn("HOLDOUT", independent_1h_clusters=600, calendar_days=25))

    def test_v1_search_space_is_bounded_and_t0_only(self):
        names = getattr(offline_replay, "V1_CANDIDATES", None)
        self.assertIsNotNone(names)
        self.assertLessEqual(len(names), 8)
        rendered = repr(names).lower()
        for forbidden in ("model_weight", "embedding", "regime_mask", "outcome", "current_price"):
            self.assertNotIn(forbidden, rendered)
