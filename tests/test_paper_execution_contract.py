"""Paper-only journal/lifecycle/recovery contract tests; never used by runtime."""
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from research.rea_engines.execution_resilience import (
    ExecutionSnapshot, PaperState, ResilienceError, SnapshotOrder,
)
from research.rea_engines.paper_execution_contract import (
    ContractError, PaperExecutionEngine, RiskLimits,
)

SOURCE = "a" * 64
CONFIG = "b" * 64


def stamp(hour):
    return f"2026-10-14T{hour:02d}:00:00Z"


def initial(cash="1000"):
    return PaperState.initial(starting_cash=cash, source_sha256=SOURCE, config_sha256=CONFIG)


def make_engine(folder, *, max_inventory="2", max_fill_qty="1"):
    return PaperExecutionEngine(
        initial_state=initial(),
        journal_path=Path(folder) / "events.jsonl",
        risk_limits=RiskLimits(max_inventory=max_inventory, max_fill_qty=max_fill_qty),
    )


def register(engine, oid="O1", qty="2", hour=0, event_id="REG-1", side="buy"):
    return engine.register_order(
        event_id=event_id, timestamp=stamp(hour), order_id=oid,
        client_order_id=f"C-{oid}", side=side, quantity=qty,
    )


def fill(engine, *, event_id, fill_id, oid="O1", qty="0.5", price="100", fee="0", hour=1, side="buy"):
    return engine.apply_fill(
        event_id=event_id, fill_id=fill_id, order_id=oid, side=side,
        quantity=qty, price=price, fee=fee, spread_cost="0", slippage_cost="0",
        timestamp=stamp(hour),
    )


class PaperExecutionLifecycleTests(unittest.TestCase):
    def test_cancel_after_partial_fill_only_closes_remainder_and_never_books(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine)
            fill(engine, event_id="EV-F1", fill_id="F1", qty="0.5")
            before = engine.state
            seq_before = engine.journal_seq
            engine.cancel_order(event_id="CANCEL-1", timestamp=stamp(2), order_id="O1", reason="operator_cancel")
            self.assertEqual(engine.status("O1"), "CANCELLED")
            self.assertEqual(engine.state, before)
            self.assertEqual(engine.journal_seq, seq_before + 1)
            with self.assertRaisesRegex(ContractError, "terminal"):
                fill(engine, event_id="EV-F2", fill_id="F2", qty="0.25", hour=3)
            self.assertEqual(engine.state, before)

    def test_expiry_is_terminal_and_has_no_accounting_side_effect(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="0.75")
            before = engine.state
            engine.expire_order(event_id="EXP-1", timestamp=stamp(1), order_id="O1", reason="ttl_elapsed")
            self.assertEqual(engine.status("O1"), "EXPIRED")
            self.assertEqual(engine.state, before)
            with self.assertRaisesRegex(ContractError, "terminal"):
                fill(engine, event_id="EV-F1", fill_id="F1", qty="0.25", hour=2)

    def test_checkpoint_recovery_replays_only_later_journal_events(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="2")
            fill(engine, event_id="EV-F1", fill_id="F1", qty="0.5")
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            fill(engine, event_id="EV-F2", fill_id="F2", qty="0.25", price="90", hour=2)
            expected_state = engine.state
            expected_status = engine.status("O1")
            expected_journal_seq = engine.journal_seq
            recovered = PaperExecutionEngine.recover(
                checkpoint_path=checkpoint, journal_path=Path(folder) / "events.jsonl",
                expected_source_sha256=SOURCE, expected_config_sha256=CONFIG,
                risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
            )
            self.assertEqual(recovered.state, expected_state)
            self.assertEqual(recovered.status("O1"), expected_status)
            self.assertEqual(recovered.journal_seq, expected_journal_seq)
            duplicate = fill(recovered, event_id="EV-F2", fill_id="F2", qty="0.25", price="90", hour=2)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(recovered.state, expected_state)

    def test_recovery_preserves_terminal_order_state_after_checkpoint(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="2")
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            engine.cancel_order(event_id="CANCEL-1", timestamp=stamp(1), order_id="O1", reason="user_request")
            recovered = PaperExecutionEngine.recover(
                checkpoint_path=checkpoint, journal_path=Path(folder) / "events.jsonl",
                expected_source_sha256=SOURCE, expected_config_sha256=CONFIG,
                risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
            )
            self.assertEqual(recovered.status("O1"), "CANCELLED")

    def test_fill_size_and_inventory_risk_limits_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder, max_inventory="0.75", max_fill_qty="0.5")
            register(engine, qty="2")
            before, seq = engine.state, engine.journal_seq
            with self.assertRaisesRegex(ContractError, "max_fill_qty"):
                fill(engine, event_id="EV-F1", fill_id="F1", qty="0.6")
            self.assertEqual(engine.state, before)
            self.assertEqual(engine.journal_seq, seq)
            fill(engine, event_id="EV-F2", fill_id="F2", qty="0.5")
            before, seq = engine.state, engine.journal_seq
            with self.assertRaisesRegex(ContractError, "max_inventory"):
                fill(engine, event_id="EV-F3", fill_id="F3", qty="0.5", hour=2)
            self.assertEqual(engine.state, before)
            self.assertEqual(engine.journal_seq, seq)

    def test_reconciliation_divergence_blocks_mutations_until_explicit_review(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            before = engine.state
            snapshot = ExecutionSnapshot(cash="999", inventory_qty="0", open_orders=(SnapshotOrder("O1", "buy", "1"),), last_event_seq=0)
            result = engine.reconcile(snapshot, event_id="SNAP-1", timestamp=stamp(1))
            self.assertFalse(result.matched)
            self.assertTrue(engine.blocked)
            with self.assertRaisesRegex(ContractError, "blocked"):
                register(engine, oid="O2", qty="0.2", hour=2, event_id="REG-2")
            self.assertEqual(engine.state, before)
            engine.resolve_divergence(
                event_id="RESOLVE-1", timestamp=stamp(2), approval_ref="AUDIT-123",
                reason="reviewed local paper state; no external state adopted",
            )
            self.assertFalse(engine.blocked)
            self.assertEqual(engine.state, before)
            # Resolution allows local paper-only continuation; it does not claim the external snapshot matched.
            register(engine, oid="O2", qty="0.2", hour=3, event_id="REG-2")

    def test_matching_snapshot_does_not_block(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            result = engine.reconcile(
                ExecutionSnapshot(cash="1000", inventory_qty="0", open_orders=(SnapshotOrder("O1", "buy", "1"),), last_event_seq=0),
                event_id="SNAP-1", timestamp=stamp(1),
            )
            self.assertTrue(result.matched, result.discrepancies)
            self.assertFalse(engine.blocked)

    def test_journal_write_failure_does_not_commit_state_and_freezes_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            before, seq = engine.state, engine.journal_seq
            with mock.patch("research.rea_engines.paper_execution_contract.os.write", side_effect=OSError("disk error")):
                with self.assertRaisesRegex(ContractError, "durab"):
                    register(engine)
            self.assertEqual(engine.state, before)
            self.assertEqual(engine.journal_seq, seq)
            self.assertTrue(engine.blocked)

    def test_same_event_id_with_different_payload_is_conflict(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1", event_id="REG-X")
            seq = engine.journal_seq
            with self.assertRaisesRegex(ContractError, "event_id.*conflict|conflict.*event_id"):
                register(engine, oid="O2", qty="1", event_id="REG-X", hour=1)
            self.assertEqual(engine.journal_seq, seq)

    def test_divergence_and_resolution_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            engine.reconcile(ExecutionSnapshot(cash="998", inventory_qty="0", open_orders=(SnapshotOrder("O1", "buy", "1"),), last_event_seq=0), event_id="SNAP-1", timestamp=stamp(1))
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            recovered = PaperExecutionEngine.recover(
                checkpoint_path=checkpoint, journal_path=Path(folder) / "events.jsonl",
                expected_source_sha256=SOURCE, expected_config_sha256=CONFIG,
                risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
            )
            self.assertTrue(recovered.blocked)
            with self.assertRaises(ContractError):
                register(recovered, oid="O2", qty="0.2", hour=2, event_id="REG-2")
            recovered.resolve_divergence(event_id="RESOLVE-1", timestamp=stamp(2), approval_ref="AUDIT-124", reason="reviewed; retain local paper ledger")
            final_checkpoint = Path(folder) / "checkpoint-final.json"
            recovered.save_checkpoint(final_checkpoint)
            recovered_again = PaperExecutionEngine.recover(
                checkpoint_path=final_checkpoint, journal_path=Path(folder) / "events.jsonl",
                expected_source_sha256=SOURCE, expected_config_sha256=CONFIG,
                risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
            )
            self.assertFalse(recovered_again.blocked)

    def test_tampered_journal_and_truncated_record_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            fill(engine, event_id="EV-F1", fill_id="F1", qty="0.2")
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            log = Path(folder) / "events.jsonl"
            data = log.read_text(encoding="utf-8")
            log.write_text(data.replace('"quantity"', '"quantity_tampered"', 1), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "hash|journal|chain|checkpoint"):
                PaperExecutionEngine.recover(checkpoint_path=checkpoint, journal_path=log, expected_source_sha256=SOURCE, expected_config_sha256=CONFIG, risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"))

        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            log = Path(folder) / "events.jsonl"
            with log.open("ab") as handle:
                handle.write(b'{"partial":')
            with self.assertRaisesRegex(ContractError, "incomplete|truncated|newline"):
                PaperExecutionEngine.recover(checkpoint_path=checkpoint, journal_path=log, expected_source_sha256=SOURCE, expected_config_sha256=CONFIG, risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"))

    def test_recovery_rejects_changed_risk_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            with self.assertRaisesRegex(ContractError, "risk.*mismatch|mismatch.*risk"):
                PaperExecutionEngine.recover(checkpoint_path=checkpoint, journal_path=Path(folder) / "events.jsonl", expected_source_sha256=SOURCE, expected_config_sha256=CONFIG, risk_limits=RiskLimits(max_inventory="3", max_fill_qty="1"))


    def test_duplicate_cancel_is_idempotent_even_after_later_events(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            self.assertTrue(engine.cancel_order(event_id="CANCEL-1", timestamp=stamp(1), order_id="O1", reason="operator_cancel"))
            seq = engine.journal_seq
            register(engine, oid="O2", qty="0.2", hour=2, event_id="REG-2")
            self.assertFalse(engine.cancel_order(event_id="CANCEL-1", timestamp=stamp(1), order_id="O1", reason="operator_cancel"))
            self.assertEqual(engine.journal_seq, seq + 1)
            self.assertEqual(engine.status("O1"), "CANCELLED")

    def test_new_event_with_backwards_timestamp_fails_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1", hour=2)
            state, seq = engine.state, engine.journal_seq
            with self.assertRaisesRegex(ContractError, "timestamp.*backwards"):
                register(engine, oid="O2", qty="0.2", hour=1, event_id="REG-2")
            self.assertEqual(engine.state, state)
            self.assertEqual(engine.journal_seq, seq)

    def test_genesis_checkpoint_can_be_recovered_before_any_event(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            recovered = PaperExecutionEngine.recover(
                checkpoint_path=checkpoint, journal_path=Path(folder) / "events.jsonl",
                expected_source_sha256=SOURCE, expected_config_sha256=CONFIG,
                risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
            )
            self.assertEqual(recovered.state, initial())
            self.assertEqual(recovered.journal_seq, 0)

    def test_partial_journal_write_freezes_engine_and_recovery_fails_closed(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            before = engine.state
            real_write = os.write
            calls = {"n": 0}
            def partial_then_fail(fd, data):
                calls["n"] += 1
                if calls["n"] == 1:
                    return real_write(fd, data[:12])
                raise OSError("simulated partial append")
            with mock.patch("research.rea_engines.paper_execution_contract.os.write", side_effect=partial_then_fail):
                with self.assertRaisesRegex(ContractError, "durability"):
                    register(engine)
            self.assertEqual(engine.state, before)
            self.assertTrue(engine.blocked)
            with self.assertRaisesRegex(ContractError, "incomplete|truncated"):
                PaperExecutionEngine.recover(
                    checkpoint_path=checkpoint, journal_path=Path(folder) / "events.jsonl",
                    expected_source_sha256=SOURCE, expected_config_sha256=CONFIG,
                    risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
                )

    def test_reconciliation_reports_order_side_remainder_and_sequence_divergence(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            result = engine.reconcile(
                ExecutionSnapshot(cash="1000", inventory_qty="0", open_orders=(SnapshotOrder("O1", "sell", "0.5"), SnapshotOrder("O-EXTRA", "buy", "0.25")), last_event_seq=4),
                event_id="SNAP-1", timestamp=stamp(1),
            )
            self.assertFalse(result.matched)
            joined = "|".join(result.discrepancies)
            self.assertIn("event_sequence_mismatch", joined)
            self.assertIn("order_side_mismatch", joined)
            self.assertIn("order_remaining_mismatch", joined)
            self.assertIn("unexpected_observed_order", joined)
            self.assertTrue(engine.blocked)



    def test_insufficient_cash_and_short_sale_fail_before_journal_commit(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder, max_inventory="2", max_fill_qty="1")
            register(engine, oid="BUY", qty="1", event_id="REG-BUY")
            before, seq = engine.state, engine.journal_seq
            with self.assertRaisesRegex(ContractError, "insufficient|cash"):
                fill(engine, event_id="EV-OVERSPEND", fill_id="F-OVERSPEND", oid="BUY", qty="0.5", price="3000")
            self.assertEqual(engine.state, before)
            self.assertEqual(engine.journal_seq, seq)
            register(engine, oid="SELL", qty="0.25", hour=1, event_id="REG-SELL", side="sell")
            before, seq = engine.state, engine.journal_seq
            with self.assertRaisesRegex(ContractError, "short|inventory"):
                fill(engine, event_id="EV-SHORT", fill_id="F-SHORT", oid="SELL", qty="0.25", price="100", hour=2, side="sell")
            self.assertEqual(engine.state, before)
            self.assertEqual(engine.journal_seq, seq)

    def test_checkpoint_state_must_match_replayed_prefix_even_if_checksum_is_recomputed(self):
        import hashlib
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            checkpoint = Path(folder) / "checkpoint.json"
            engine.save_checkpoint(checkpoint)
            envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
            envelope["engine_state"]["state"]["cash"] = "999"
            canonical = json.dumps(envelope["engine_state"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            envelope["state_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            checkpoint.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            with self.assertRaisesRegex(ContractError, "checkpoint.*(match|prefix)|prefix"):
                PaperExecutionEngine.recover(
                    checkpoint_path=checkpoint, journal_path=Path(folder) / "events.jsonl",
                    expected_source_sha256=SOURCE, expected_config_sha256=CONFIG,
                    risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
                )

    def test_checkpoint_cannot_overwrite_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            before = (Path(folder) / "events.jsonl").read_bytes()
            with self.assertRaisesRegex(ContractError, "differ from journal"):
                engine.save_checkpoint(Path(folder) / "events.jsonl")
            self.assertEqual((Path(folder) / "events.jsonl").read_bytes(), before)



    def test_stale_second_writer_is_blocked_before_append(self):
        with tempfile.TemporaryDirectory() as folder:
            first = make_engine(folder)
            second = PaperExecutionEngine(
                initial_state=initial(), journal_path=Path(folder) / "events.jsonl",
                risk_limits=RiskLimits(max_inventory="2", max_fill_qty="1"),
            )
            register(first, qty="1")
            before = second.state
            with self.assertRaisesRegex(ContractError, "writer conflict|stale head"):
                register(second, oid="O2", qty="0.2", hour=1, event_id="REG-2")
            self.assertEqual(second.state, before)
            self.assertTrue(second.blocked)
            self.assertEqual(first.journal_seq, 1)



    def test_journal_physical_byte_count_matches_appender_offset_on_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            engine = make_engine(folder)
            register(engine, qty="1")
            self.assertEqual((Path(folder) / "events.jsonl").stat().st_size, engine._journal_size)
            fill(engine, event_id="EV-F1", fill_id="F1", qty="0.25")
            self.assertEqual((Path(folder) / "events.jsonl").stat().st_size, engine._journal_size)



if __name__ == "__main__":
    unittest.main()
