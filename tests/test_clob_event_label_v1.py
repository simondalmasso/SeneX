from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "senecio_polymarket" / "frontend" / "app.js"
INDEX = ROOT / "senecio_polymarket" / "frontend" / "index.html"


def _app() -> str:
    return APP.read_text(encoding="utf-8")


def _index() -> str:
    return INDEX.read_text(encoding="utf-8")


def test_ambiguous_price_error_label_removed():
    assert "event without price fields" not in _app()


def test_metadata_only_events_are_labeled_explicitly():
    assert "metadata event (no price fields)" in _app()


def test_metadata_only_count_is_exposed_in_header():
    app = _app()
    assert "metadataOnlyCount" in app
    assert "poly-feed-meta" in app
    assert 'id="poly-feed-meta"' in _index()
