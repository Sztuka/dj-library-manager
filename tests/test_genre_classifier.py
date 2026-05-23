from __future__ import annotations

from unittest.mock import patch

from djlib.metadata import genre_classifier as gc


def test_classify_genre_returns_validated_year_from_web_search():
    ws_ctx = """
[DISCOGS] Test Artist - Test Title (2024 release)
  URL: https://www.discogs.com/release/123
  Released: 2024 on Example Records
""".strip()

    with patch.object(gc, "get_genre_classifier_provider", return_value="openai"), \
         patch.object(gc, "get_openai_api_key", return_value="test-key"), \
         patch.object(gc, "_fetch_web_search", return_value=ws_ctx), \
         patch.object(gc, "_fetch_lastfm_tags", return_value=""), \
         patch.object(gc, "_genre_labels", return_value=["Deep House", "House"]), \
         patch.object(gc, "_call_openai", return_value={
             "genre": "Deep House",
             "confidence": 0.84,
             "reasoning": "Discogs and artist knowledge point to Deep House",
             "year": "2024-06-15",
             "year_evidence": "Discogs snippet says Released: 2024",
         }):
        result = gc.classify_genre("Test Artist", "Test Title", version="Extended Mix")

    assert result["genre"] == "Deep House"
    assert result["year"] == "2024"
    assert result["year_evidence"] == "Discogs snippet says Released: 2024"


def test_classify_genre_keeps_year_when_web_snippets_lack_year():
    """Web snippets often omit release year — we trust the model's training-knowledge
    year as a fallback rather than discarding it. Confidence calibration happens
    at the call site (batch enrichment caps conf to 0.6 for non-WS-backed years)."""
    ws_ctx = """
[DISCOGS] Test Artist - Test Title
  URL: https://www.discogs.com/release/123
  Classic house release from the early days.
""".strip()

    with patch.object(gc, "get_genre_classifier_provider", return_value="openai"), \
         patch.object(gc, "get_openai_api_key", return_value="test-key"), \
         patch.object(gc, "_fetch_web_search", return_value=ws_ctx), \
         patch.object(gc, "_fetch_lastfm_tags", return_value=""), \
         patch.object(gc, "_genre_labels", return_value=["House"]), \
         patch.object(gc, "_call_openai", return_value={
             "genre": "House",
             "confidence": 0.6,
             "reasoning": "Weak signals",
             "year": "2024",
             "year_evidence": "Training knowledge: artist's debut era",
         }):
        result = gc.classify_genre("Test Artist", "Test Title")

    assert result["genre"] == "House"
    assert result["year"] == "2024"
    assert result["year_evidence"] == "Training knowledge: artist's debut era"
