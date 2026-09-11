"""Frontend truth-state tests (takeover section 27).

Exercises the pure dashboard truth models (frontend/dashboard_truth.js) in
Node: UNKNOWN propagation (never zero, never OK), paper-view honesty, and
safety-view invariants including the B8.1 hard paper lock.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

TRUTH_JS = (
    Path(__file__).resolve().parent.parent
    / "senecio_polymarket" / "frontend" / "dashboard_truth.js"
)

NODE_SCRIPT = r"""
const truth = require(process.argv[1]);
const cases = {
  ok: {
    status: "OK", hypothetical: true,
    safety: { hard_paper_lock: true, trade_mode: "PAPER", orders_enabled: false, live_capital_locked: true },
    execution: { starting_cash: 10000, cash: 9950, open_positions: 1, total_orders: 2, taker_fee_bps: 5 },
    equity: 10001.25,
    edge: { p_market: 0.485, p_market_source: "DECISION_TIME_AUDIT", p_senex: 0.26, incremental_edge: -0.225, status: "UNPROVEN" },
    model_quality: { observation_count: 4, abstentions: 4, resolved: 0, brier_score: "NOT_COMPUTED_UNTIL_OUTCOMES_RESOLVE" },
  },
  noPipeline: { status: "NO_PORTFOLIO_PIPELINE", hypothetical: true, safety: { hard_paper_lock: true } },
  null: null,
};
const out = {};
const ok = truth.paperView(cases.ok);
out.ok = {
  bankroll: ok.bankroll, equity: ok.equity, lock: ok.lock,
  incremental: ok.incremental, brier: ok.brier, edgeStatus: ok.edgeStatus,
  hypothetical: ok.hypothetical,
};
const noPipeline = truth.paperView(cases.noPipeline);
out.noPipeline = { status: noPipeline.status, bankroll: noPipeline.bankroll, lock: noPipeline.lock, pnl: noPipeline.pnl };
const empty = truth.paperView(cases.null);
out.empty = { status: empty.status, bankroll: empty.bankroll, lock: empty.lock, pMarket: empty.pMarket, observations: empty.observations };
const safety = truth.safetyView({
  mode: "REAL_ONLY",
  safety: { trade_mode: "PAPER", live_capital_locked: true, orders_enabled: false, read_only_market_adapters: true },
});
out.safety = { tradeMode: safety.tradeMode, liveCapital: safety.liveCapital, orders: safety.orders, readOnly: safety.readOnlyAdapters };
const scoreUnknown = truth.scoreView({});
out.scoreUnknown = { status: scoreUnknown.status, authorityWr: scoreUnknown.authorityWr, scope: scoreUnknown.scope };
console.log(JSON.stringify(out));
"""


def _run_node() -> dict:
    result = subprocess.run(
        ["node", "-e", NODE_SCRIPT, str(TRUTH_JS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


class FrontendTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.views = _run_node()
        except FileNotFoundError as exc:
            self.skipTest(f"node unavailable: {exc}")

    def test_paper_view_ok_payload(self) -> None:
        ok = self.views["ok"]
        self.assertEqual(ok["bankroll"], "$10,000")
        self.assertEqual(ok["equity"], "$10,001.25")
        self.assertEqual(ok["lock"], "ENGAGED")
        self.assertEqual(ok["incremental"], "-22.50pp")
        self.assertEqual(ok["brier"], "NOT COMPUTED")
        self.assertEqual(ok["edgeStatus"], "UNPROVEN")
        self.assertTrue(ok["hypothetical"])

    def test_paper_view_missing_pipeline_is_unknown_not_zero(self) -> None:
        no = self.views["noPipeline"]
        self.assertEqual(no["status"], "NO_PORTFOLIO_PIPELINE")
        self.assertEqual(no["bankroll"], "UNKNOWN")
        self.assertEqual(no["pnl"], "UNKNOWN")
        self.assertEqual(no["lock"], "ENGAGED")

    def test_paper_view_null_payload_all_unknown(self) -> None:
        empty = self.views["empty"]
        self.assertEqual(empty["status"], "UNKNOWN")
        for key in ("bankroll", "lock", "pMarket", "observations"):
            self.assertEqual(empty[key], "UNKNOWN", key)

    def test_safety_view_invariants(self) -> None:
        safety = self.views["safety"]
        self.assertEqual(safety["tradeMode"], "PAPER")
        self.assertEqual(safety["liveCapital"], "LOCKED")
        self.assertEqual(safety["orders"], "DISABLED")
        self.assertEqual(safety["readOnly"], "READ-ONLY")

    def test_score_view_unknown_propagation_unchanged(self) -> None:
        score = self.views["scoreUnknown"]
        self.assertEqual(score["status"], "UNKNOWN")
        self.assertEqual(score["authorityWr"], "—")
        self.assertEqual(score["scope"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
