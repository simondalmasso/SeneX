"""B8.1 HARD PAPER LOCK regression battery (section 4 / 14 of the takeover).

Proves that attempts to activate live execution via environment variables,
config values, runtime state, LiveGate results, or direct API misuse cannot
produce wallet signatures, real orders, or fund movement paths in this
candidate. The lock is structural (backend/paper_lock.py module constant).
"""
from __future__ import annotations

import os
import unittest
from unittest import mock

from senecio_polymarket.backend import paper_lock
from senecio_polymarket.backend.portfolio.live_gate import LiveGate
from senecio_polymarket.backend.portfolio.execution_engine import ExecutionEngine
from senecio_polymarket.backend.execution_simulator import ExecutionSimulator

PASSING_SCORE = {
    "score_scope": "PER_SYMBOL",
    "requested_symbol": "BTCUSDT",
    "authority_1h": {
        "n_source": "INDEPENDENT_NONOVERLAP_1H",
        "global": {
            "n_source": "INDEPENDENT_NONOVERLAP_1H",
            "win_rate_pct": 60.0,
            "verified": 500,
        },
    },
}
PASSING_ANALYTICS = {"profit_factor": 1.8, "max_drawdown_pct": 2.0}


def _all_six_pass_gate() -> object:
    return LiveGate().evaluate(
        oracle_score=PASSING_SCORE,
        analytics_report=PASSING_ANALYTICS,
        shadow_report={"passed": True},
        exec_self_test={"verified": True},
    )


class HardPaperLockTests(unittest.TestCase):
    def test_lock_is_active_and_not_env_configurable(self) -> None:
        self.assertTrue(paper_lock.hard_paper_lock_active())
        hostile_env = {
            "SENEX_TRADE_MODE": "LIVE",
            "SENEX_ORDERS_ENABLED": "true",
            "SENEX_LIVE_CAPITAL_LOCKED": "false",
            "SENEX_HARD_PAPER_LOCK": "false",
            "SENEX_ALLOW_LIVE": "1",
            "SENEX_ALLOW_REAL": "1",
            "LIVE_GATE_UNLOCK": "1",
        }
        with mock.patch.dict(os.environ, hostile_env, clear=False):
            # Re-import semantics: the lock is a module constant; env cannot flip it.
            self.assertTrue(paper_lock.hard_paper_lock_active())
            status = _all_six_pass_gate()
            self.assertFalse(status.unlocked)

    def test_live_gate_with_all_six_conditions_passing_stays_locked(self) -> None:
        status = _all_six_pass_gate()
        self.assertFalse(status.unlocked)
        self.assertEqual(status.trade_mode, "PAPER")
        self.assertTrue(status.live_capital_locked)
        self.assertIn("HARD_PAPER_LOCK", ";".join(status.failed_reasons))

    def test_live_gate_locked_by_default_construction(self) -> None:
        gate = LiveGate()
        status = gate.evaluate()
        self.assertFalse(status.unlocked)
        self.assertEqual(status.trade_mode, "PAPER")

    def test_execution_engine_enable_live_mode_refused(self) -> None:
        engine = ExecutionEngine()
        with self.assertRaises(paper_lock.HardPaperLockError):
            engine.enable_live_mode(unlocked_by="LIVE_GATE")
        self.assertFalse(engine.cfg["allow_live"])
        self.assertEqual(engine.cfg["trade_mode"], "PAPER")

    def test_execution_engine_update_config_cannot_flip_capital_mode(self) -> None:
        engine = ExecutionEngine()
        with self.assertRaises(RuntimeError):
            engine.update_config(allow_live=True)
        with self.assertRaises(RuntimeError):
            engine.update_config(trade_mode="LIVE")
        with self.assertRaises(RuntimeError):
            engine.update_config(live_capital_locked=False)
        # Safe updates still work
        engine.update_config(trade_mode="PAPER", taker_fee_bps=5.0)
        self.assertEqual(engine.cfg["taker_fee_bps"], 5.0)
        self.assertFalse(engine.cfg["allow_live"])

    def test_execution_engine_smuggled_live_cfg_refused_at_order_path(self) -> None:
        import asyncio

        engine = ExecutionEngine()
        # Smuggle allow_live=True directly into the cfg dict (bypassing API):
        engine.cfg["allow_live"] = True
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(engine.submit({}, {}, last_price=1.0))
        self.assertIn("LIVE_GATE unlock", str(ctx.exception))

        # Smuggle trade_mode=LIVE with allow_live back to False:
        engine.cfg["allow_live"] = False
        engine.cfg["trade_mode"] = "LIVE"
        with self.assertRaises(RuntimeError) as ctx:
            asyncio.run(engine.submit({}, {}, last_price=1.0))
        self.assertIn("HARD_PAPER_LOCK", str(ctx.exception))

        # Restore honest state; paper path guard no longer trips on capital mode.
        engine.cfg["trade_mode"] = "PAPER"

    def test_execution_simulator_construction_with_allow_real_refused(self) -> None:
        with self.assertRaises(paper_lock.HardPaperLockError):
            ExecutionSimulator(allow_real=True)

    def test_execution_simulator_mutation_of_allow_real_refused(self) -> None:
        sim = ExecutionSimulator()
        with self.assertRaises(paper_lock.HardPaperLockError):
            sim.allow_real = True
        self.assertFalse(sim.allow_real)

    def test_safety_projection_shape(self) -> None:
        safety = paper_lock.safety_projection()
        self.assertEqual(safety["trade_mode"], "PAPER")
        self.assertFalse(safety["orders_enabled"])
        self.assertTrue(safety["live_capital_locked"])
        self.assertTrue(safety["hard_paper_lock"])
        self.assertEqual(safety["hard_paper_lock_version"], paper_lock.HARD_PAPER_LOCK_VERSION)


if __name__ == "__main__":
    unittest.main()
