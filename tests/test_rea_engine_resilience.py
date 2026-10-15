import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from research.rea_engines.contracts import Candle
from research.rea_engines.execution_resilience import (
    ExecutionSnapshot,
    FillEvent,
    IntrabarPolicy,
    PaperState,
    ResilienceError,
    SnapshotOrder,
    apply_fill,
    load_checkpoint,
    reconcile_snapshot,
    register_order,
    resolve_long_bracket,
    save_checkpoint,
)


SOURCE_SHA = "a" * 64
CONFIG_SHA = "b" * 64


def candle(hour, open_, high, low, close):
    return Candle(
        timestamp=f"2026-10-14T{hour:02d}:00:00Z",
        open=open_, high=high, low=low, close=close, volume="100",
    )


def make_state(cash="1000"):
    return PaperState.initial(
        starting_cash=cash,
        source_sha256=SOURCE_SHA,
        config_sha256=CONFIG_SHA,
    )


def fill(fill_id, order_id, side, qty, price, fee, seq, ts_hour=1):
    return FillEvent(
        fill_id=fill_id,
        order_id=order_id,
        side=side,
        quantity=qty,
        price=price,
        fee=fee,
        spread_cost="0",
        slippage_cost="0",
        timestamp=f"2026-10-14T{ts_hour:02d}:00:00Z",
        event_seq=seq,
    )


class IntrabarAmbiguityTests(unittest.TestCase):
    def test_both_stop_and_target_touched_uses_conservative_stop_first(self):
        result = resolve_long_bracket(candle(0, "100", "115", "85", "104"), "90", "110")
        self.assertEqual(result.trigger, "STOP")
        self.assertEqual(result.fill_price, Decimal("90"))
        self.assertTrue(result.ambiguous)
        self.assertEqual(result.resolution_policy, IntrabarPolicy.WORST_CASE_STOP_FIRST.value)

    def test_both_touched_can_be_rejected_instead_of_assuming_intrabar_order(self):
        result = resolve_long_bracket(
            candle(0, "100", "115", "85", "104"), "90", "110",
            policy=IntrabarPolicy.REJECT_AMBIGUOUS,
        )
        self.assertEqual(result.trigger, "AMBIGUOUS")
        self.assertIsNone(result.fill_price)
        self.assertTrue(result.ambiguous)

    def test_open_gap_through_long_stop_fills_at_open_not_the_better_stop_level(self):
        result = resolve_long_bracket(candle(0, "85", "115", "80", "100"), "90", "110")
        self.assertEqual(result.trigger, "STOP")
        self.assertEqual(result.fill_price, Decimal("85"))
        self.assertTrue(result.ambiguous is False)

    def test_single_target_touch_is_not_marked_ambiguous(self):
        result = resolve_long_bracket(candle(0, "100", "112", "95", "108"), "90", "110")
        self.assertEqual(result.trigger, "TARGET")
        self.assertEqual(result.fill_price, Decimal("110"))
        self.assertFalse(result.ambiguous)


class PartialFillAndIdempotencyTests(unittest.TestCase):
    def test_partial_fill_updates_remaining_quantity_and_decimal_ledger(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="2")
        state, first = apply_fill(state, fill("F-1", "O-1", "buy", "0.75", "100", "0.75", 1))
        self.assertTrue(first.applied)
        self.assertEqual(first.status, "PARTIALLY_FILLED")
        self.assertEqual(first.remaining_qty, Decimal("1.25"))
        self.assertEqual(state.cash, Decimal("924.25"))
        self.assertEqual(state.inventory_qty, Decimal("0.75"))
        state, second = apply_fill(state, fill("F-2", "O-1", "buy", "0.5", "90", "0.45", 2))
        self.assertTrue(second.applied)
        self.assertEqual(second.status, "PARTIALLY_FILLED")
        self.assertEqual(second.remaining_qty, Decimal("0.75"))
        self.assertEqual(state.cash, Decimal("878.80"))
        self.assertEqual(state.inventory_qty, Decimal("1.25"))
        self.assertEqual(state.inventory_cost_basis, Decimal("121.20"))

    def test_exact_final_partial_slice_transitions_order_to_filled(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="1")
        state, result = apply_fill(state, fill("F-1", "O-1", "buy", "0.4", "100", "0", 1))
        self.assertEqual(result.status, "PARTIALLY_FILLED")
        state, result = apply_fill(state, fill("F-2", "O-1", "buy", "0.6", "100", "0", 2))
        self.assertEqual(result.status, "FILLED")
        self.assertEqual(result.remaining_qty, Decimal("0"))
        self.assertEqual(state.inventory_qty, Decimal("1"))

    def test_duplicate_fill_id_is_idempotent_and_does_not_double_book(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="1")
        event = fill("F-1", "O-1", "buy", "0.5", "100", "0.5", 1)
        state, result = apply_fill(state, event)
        before = state
        state, duplicate = apply_fill(state, event)
        self.assertFalse(duplicate.applied)
        self.assertTrue(duplicate.duplicate)
        self.assertEqual(state, before)
        self.assertEqual(state.cash, Decimal("949.5"))
        self.assertEqual(state.inventory_qty, Decimal("0.5"))

    def test_unseen_event_sequence_gap_fails_closed_without_state_change(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="2")
        before = state
        with self.assertRaisesRegex(ResilienceError, "sequence"):
            apply_fill(state, fill("F-2", "O-1", "buy", "0.5", "100", "0", 2))
        self.assertEqual(state, before)

    def test_overfill_is_rejected_atomically(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="1")
        before = state
        with self.assertRaisesRegex(ResilienceError, "remaining"):
            apply_fill(state, fill("F-1", "O-1", "buy", "1.1", "100", "0", 1))
        self.assertEqual(state, before)

    def test_submission_by_same_client_id_is_idempotent_but_conflict_fails(self):
        state = make_state()
        state = register_order(state, order_id="O-1", client_order_id="C-1", side="buy", quantity="1")
        before = state
        self.assertEqual(
            register_order(state, order_id="O-1", client_order_id="C-1", side="buy", quantity="1"),
            before,
        )
        with self.assertRaisesRegex(ResilienceError, "conflict"):
            register_order(state, order_id="O-2", client_order_id="C-1", side="buy", quantity="2")


class RestartAndReconciliationTests(unittest.TestCase):
    def test_checkpoint_round_trip_then_duplicate_event_is_not_reapplied(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="2")
        event = fill("F-1", "O-1", "buy", "0.75", "100", "0.75", 1)
        state, _ = apply_fill(state, event)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            saved_digest = save_checkpoint(path, state)
            resumed = load_checkpoint(path, expected_source_sha256=SOURCE_SHA, expected_config_sha256=CONFIG_SHA)
            self.assertEqual(resumed, state)
            self.assertEqual(saved_digest, resumed.checkpoint_sha256())
            resumed, duplicate = apply_fill(resumed, event)
            self.assertTrue(duplicate.duplicate)
            self.assertEqual(resumed.cash, Decimal("924.25"))
            resumed, next_fill = apply_fill(resumed, fill("F-2", "O-1", "buy", "0.25", "90", "0.225", 2))
            self.assertTrue(next_fill.applied)
            self.assertEqual(resumed.inventory_qty, Decimal("1"))
            self.assertEqual(resumed.last_event_seq, 2)

    def test_checkpoint_refuses_wrong_source_or_config(self):
        state = make_state()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            save_checkpoint(path, state)
            with self.assertRaisesRegex(ResilienceError, "source"):
                load_checkpoint(path, expected_source_sha256="c" * 64, expected_config_sha256=CONFIG_SHA)
            with self.assertRaisesRegex(ResilienceError, "config"):
                load_checkpoint(path, expected_source_sha256=SOURCE_SHA, expected_config_sha256="d" * 64)

    def test_tampered_checkpoint_fails_checksum_validation(self):
        state = make_state()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "checkpoint.json"
            save_checkpoint(path, state)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["state"]["cash"] = "1"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ResilienceError, "checksum"):
                load_checkpoint(path, expected_source_sha256=SOURCE_SHA, expected_config_sha256=CONFIG_SHA)

    def test_snapshot_matches_partial_fill_state(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="2")
        state, _ = apply_fill(state, fill("F-1", "O-1", "buy", "0.5", "100", "0.5", 1))
        snapshot = ExecutionSnapshot(
            cash=state.cash,
            inventory_qty=state.inventory_qty,
            open_orders=(SnapshotOrder(order_id="O-1", side="buy", remaining_qty="1.5"),),
            last_event_seq=1,
        )
        result = reconcile_snapshot(state, snapshot)
        self.assertTrue(result.matched, result.discrepancies)
        self.assertEqual(result.discrepancies, ())

    def test_snapshot_mismatch_is_reported_without_mutating_local_state(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="2")
        before = state
        snapshot = ExecutionSnapshot(
            cash="999",
            inventory_qty="0",
            open_orders=(SnapshotOrder(order_id="O-99", side="buy", remaining_qty="1"),),
            last_event_seq=4,
        )
        result = reconcile_snapshot(state, snapshot)
        self.assertFalse(result.matched)
        self.assertGreaterEqual(len(result.discrepancies), 3)
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()


class AdditionalResilienceContractTests(unittest.TestCase):
    def test_replaying_same_fill_id_with_different_payload_is_conflict(self):
        state = register_order(make_state(), order_id="O-1", client_order_id="C-1", side="buy", quantity="2")
        state, _ = apply_fill(state, fill("F-1", "O-1", "buy", "0.5", "100", "0.5", 1))
        conflicting_duplicate = fill("F-1", "O-1", "buy", "0.75", "100", "0.75", 1)
        before = state
        with self.assertRaisesRegex(ResilienceError, "conflict"):
            apply_fill(state, conflicting_duplicate)
        self.assertEqual(state, before)

    def test_exact_cash_consumption_allows_zero_cash_checkpoint_state(self):
        state = register_order(make_state("1000"), order_id="O-1", client_order_id="C-1", side="buy", quantity="10")
        state, result = apply_fill(state, fill("F-1", "O-1", "buy", "10", "100", "0", 1))
        self.assertTrue(result.applied)
        self.assertEqual(state.cash, Decimal("0"))
        self.assertEqual(state.inventory_qty, Decimal("10"))

    def test_target_first_is_an_explicit_optimistic_sensitivity_case(self):
        result = resolve_long_bracket(
            candle(0, "100", "115", "85", "104"), "90", "110",
            policy=IntrabarPolicy.BEST_CASE_TARGET_FIRST,
        )
        self.assertEqual(result.trigger, "TARGET")
        self.assertEqual(result.fill_price, Decimal("110"))
        self.assertTrue(result.ambiguous)


    def test_partial_sell_allocates_basis_and_realized_net_pnl_exactly_once(self):
        state = register_order(make_state(), order_id="O-BUY", client_order_id="C-BUY", side="buy", quantity="1")
        state, buy = apply_fill(state, fill("F-BUY", "O-BUY", "buy", "1", "100", "1", 1))
        self.assertTrue(buy.applied)
        state = register_order(state, order_id="O-SELL", client_order_id="C-SELL", side="sell", quantity="1")
        state, first = apply_fill(state, fill("F-SELL-1", "O-SELL", "sell", "0.4", "120", "0.48", 2, ts_hour=2))
        self.assertEqual(first.status, "PARTIALLY_FILLED")
        self.assertEqual(state.inventory_qty, Decimal("0.6"))
        self.assertEqual(state.inventory_cost_basis, Decimal("60.6"))
        self.assertEqual(state.realized_pnl, Decimal("7.12"))
        state, second = apply_fill(state, fill("F-SELL-2", "O-SELL", "sell", "0.6", "120", "0.72", 3, ts_hour=3))
        self.assertEqual(second.status, "FILLED")
        self.assertEqual(state.inventory_qty, Decimal("0"))
        self.assertEqual(state.inventory_cost_basis, Decimal("0"))
        self.assertEqual(state.cash, Decimal("1017.80"))
        self.assertEqual(state.realized_pnl, Decimal("17.80"))
        self.assertEqual(state.total_fees, Decimal("2.20"))
