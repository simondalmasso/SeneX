from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "research" / "edge" / "order098" / "polymarket_5m_resolutions.py"


def _load():
    spec = importlib.util.spec_from_file_location("order098_resolutions", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _event(
    *,
    start_ts=1791069600,
    condition_id="0xabc",
    up_price="1",
    down_price="0",
    closed=True,
    status="resolved",
    closed_time="2026-10-03 23:26:26+00",
    uma_end="2026-10-03T23:26:26Z",
    final_price=84789.80963756573,
    price_to_beat=84746.84245287585,
):
    slug = f"btc-updown-5m-{start_ts}"
    return {
        "id": "event-1",
        "slug": slug,
        "closed": closed,
        "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
        "eventMetadata": {
            "finalPrice": final_price,
            "priceToBeat": price_to_beat,
        },
        "markets": [
            {
                "id": "market-1",
                "slug": slug,
                "conditionId": condition_id,
                "closed": closed,
                "outcomes": json.dumps(["Up", "Down"]),
                "outcomePrices": json.dumps([up_price, down_price]),
                "umaResolutionStatus": status,
                "closedTime": closed_time,
                "umaEndDate": uma_end,
                "resolutionSource": "https://data.chain.link/streams/btc-usd-twap-60s-streams",
                "eventStartTime": "2026-10-03T23:20:00Z",
            }
        ],
    }


def _prediction(
    *,
    start_ts=1791069600,
    condition_id="0xabc",
    senex_outcome="LOSS",
):
    slug = f"btc-updown-5m-{start_ts}"
    return {
        "id": 7,
        "ts": "2026-10-03T23:20:30Z",
        "symbol": "BTCUSDT",
        "outcome": senex_outcome,
        "_audit": {
            "external_markets_v1": {
                "polymarket": {
                    "source": "POLYMARKET_PUBLIC",
                    "version": "polymarket-btc-5m-v1",
                    "eligible_for_prediction": True,
                    "slug": slug,
                    "condition_id": condition_id,
                    "start_ts": start_ts,
                    "end_ts": start_ts + 300,
                }
            }
        },
    }


def test_normalizes_terminal_up_resolution_with_evidence_hash():
    m = _load()
    result = m.normalize_gamma_event(
        _event(),
        expected_slug="btc-updown-5m-1791069600",
        expected_condition_id="0xabc",
        fetched_at="2026-10-04T02:00:00Z",
    )

    assert result["slug"] == "btc-updown-5m-1791069600"
    assert result["condition_id"] == "0xabc"
    assert result["start_ts"] == 1791069600
    assert result["end_ts"] == 1791069900
    assert result["outcome"] == "UP"
    assert result["resolved_at"] >= result["end_ts"]
    assert result["source"] == "POLYMARKET_GAMMA_RESOLVED_V1"
    assert len(result["evidence_sha256"]) == 64
    assert result["resolution_source_url"].startswith("https://data.chain.link/")


def test_normalizes_terminal_down_resolution():
    m = _load()
    result = m.normalize_gamma_event(
        _event(
            start_ts=1791069900,
            condition_id="0xdef",
            up_price="0",
            down_price="1",
            final_price=84726.08982787756,
            price_to_beat=84789.80963756573,
            closed_time="2026-10-03 23:31:35+00",
            uma_end="2026-10-03T23:31:35Z",
        ),
        expected_slug="btc-updown-5m-1791069900",
        expected_condition_id="0xdef",
    )
    assert result["outcome"] == "DOWN"


@pytest.mark.parametrize(
    "event,match",
    [
        (_event(closed=False), "closed"),
        (_event(status="proposed"), "resolved"),
        (_event(up_price="0.7", down_price="0.3"), "terminal"),
        (_event(up_price="1", down_price="1"), "terminal"),
        (
            _event(
                up_price="1",
                down_price="0",
                final_price=84000,
                price_to_beat=85000,
            ),
            "conflicts",
        ),
    ],
)
def test_resolution_contract_fails_closed(event, match):
    m = _load()
    with pytest.raises(m.ResolutionEvidenceError, match=match):
        m.normalize_gamma_event(
            event,
            expected_slug="btc-updown-5m-1791069600",
            expected_condition_id="0xabc",
        )


def test_rejects_identity_mismatch():
    m = _load()
    with pytest.raises(m.ResolutionEvidenceError, match="condition"):
        m.normalize_gamma_event(
            _event(condition_id="0xwrong"),
            expected_slug="btc-updown-5m-1791069600",
            expected_condition_id="0xabc",
        )


def test_rejects_preclose_resolution_timestamp():
    m = _load()
    with pytest.raises(m.ResolutionEvidenceError, match="before market end"):
        m.normalize_gamma_event(
            _event(
                closed_time="2026-10-03 23:24:59+00",
                uma_end="2026-10-03T23:24:59Z",
            ),
            expected_slug="btc-updown-5m-1791069600",
            expected_condition_id="0xabc",
        )


def test_prediction_inventory_uses_only_exact_polymarket_identity_not_senex_outcome():
    m = _load()
    a = m.extract_market_identity(_prediction(senex_outcome="WIN"))
    b = m.extract_market_identity(_prediction(senex_outcome="LOSS"))

    assert a == b
    assert a == {
        "slug": "btc-updown-5m-1791069600",
        "condition_id": "0xabc",
        "start_ts": 1791069600,
        "end_ts": 1791069900,
    }


@pytest.mark.parametrize(
    "mutator",
    [
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"source": "OTHER"}
        ),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"version": "unknown"}
        ),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"eligible_for_prediction": False}
        ),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"end_ts": 1791070200}
        ),
        lambda row: row["_audit"]["external_markets_v1"]["polymarket"].update(
            {"start_ts": 1791069601, "end_ts": 1791069901, "slug": "btc-updown-5m-1791069601"}
        ),
    ],
)
def test_prediction_inventory_rejects_non_target_rows(mutator):
    m = _load()
    row = _prediction()
    mutator(row)
    assert m.extract_market_identity(row) is None


def test_deduped_inventory_is_deterministic_and_rejects_conflicting_identity():
    m = _load()
    rows = [
        _prediction(start_ts=1791069900, condition_id="0xdef"),
        _prediction(start_ts=1791069600, condition_id="0xabc"),
        _prediction(start_ts=1791069600, condition_id="0xabc"),
    ]
    inventory = m.extract_market_identities(rows)
    assert [item["start_ts"] for item in inventory] == [1791069600, 1791069900]

    conflict = _prediction(start_ts=1791069600, condition_id="0xother")
    with pytest.raises(m.ResolutionEvidenceError, match="conflicting"):
        m.extract_market_identities([_prediction(), conflict])


def test_write_jsonl_is_sorted_and_stable(tmp_path):
    m = _load()
    records = [
        m.normalize_gamma_event(
            _event(
                start_ts=1791069900,
                condition_id="0xdef",
                up_price="0",
                down_price="1",
                final_price=1,
                price_to_beat=2,
                closed_time="2026-10-03 23:31:35+00",
                uma_end="2026-10-03T23:31:35Z",
            ),
            expected_slug="btc-updown-5m-1791069900",
            expected_condition_id="0xdef",
        ),
        m.normalize_gamma_event(
            _event(),
            expected_slug="btc-updown-5m-1791069600",
            expected_condition_id="0xabc",
        ),
    ]
    path = tmp_path / "resolutions.jsonl"
    m.write_jsonl(path, records)
    first = path.read_text(encoding="utf-8")
    m.write_jsonl(path, list(reversed(records)))
    second = path.read_text(encoding="utf-8")

    assert first == second
    parsed = [json.loads(line) for line in first.splitlines()]
    assert [row["start_ts"] for row in parsed] == [1791069600, 1791069900]


def test_manifest_binds_exact_output_file_sha256(tmp_path):
    m = _load()
    record = m.normalize_gamma_event(
        _event(),
        expected_slug="btc-updown-5m-1791069600",
        expected_condition_id="0xabc",
    )
    output = tmp_path / "resolutions.jsonl"
    m.write_jsonl(output, [record])

    manifest = m._manifest(
        identities=[{
            "slug": record["slug"],
            "condition_id": record["condition_id"],
            "start_ts": record["start_ts"],
            "end_ts": record["end_ts"],
        }],
        accepted=[record],
        rejected=[],
        predictions_path=None,
        output_path=output,
    )

    assert len(manifest["output_file_sha256"]) == 64
    assert manifest["output_file_sha256"] == m._file_sha256(output)


@pytest.mark.parametrize(
    "final_price,price_to_beat",
    [
        ("MALFORMED", 84746.0),
        (84789.0, "MALFORMED"),
        (float("nan"), 84746.0),
        (84789.0, float("inf")),
        (float("-inf"), 84746.0),
        (84789.0, None),
        (None, 84746.0),
    ],
)
def test_present_gamma_metadata_must_be_complete_finite_and_parseable(
    final_price, price_to_beat
):
    m = _load()
    event = _event(final_price=final_price, price_to_beat=price_to_beat)
    with pytest.raises(m.ResolutionEvidenceError, match="metadata"):
        m.normalize_gamma_event(
            event,
            expected_slug="btc-updown-5m-1791069600",
            expected_condition_id="0xabc",
        )


def test_absent_gamma_metadata_is_explicitly_optional():
    m = _load()
    event = _event()
    event.pop("eventMetadata")
    result = m.normalize_gamma_event(
        event,
        expected_slug="btc-updown-5m-1791069600",
        expected_condition_id="0xabc",
    )
    assert result["outcome"] == "UP"
    assert result["final_price"] is None
    assert result["price_to_beat"] is None
