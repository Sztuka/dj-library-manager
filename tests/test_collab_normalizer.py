"""Tests for collaboration format normalization."""
from __future__ import annotations

import pytest
from djlib.collab_normalizer import normalize_collab_artist, collect_normalization_candidates


# ── normalize_collab_artist ────────────────────────────────────────────────────

def _norm(raw, overrides=None, use_mb=False):
    """Shorthand: normalize without MB lookup."""
    result, changed = normalize_collab_artist(raw, overrides=overrides or {}, use_mb=use_mb)
    return result, changed


def test_ft_dot_normalized():
    r, c = _norm("Calvin Harris ft. Dua Lipa")
    assert r == "Calvin Harris feat. Dua Lipa"
    assert c is True


def test_ft_no_dot_normalized():
    r, c = _norm("Calvin Harris ft Dua Lipa")
    assert r == "Calvin Harris feat. Dua Lipa"
    assert c is True


def test_featuring_normalized():
    r, c = _norm("Calvin Harris featuring Dua Lipa")
    assert r == "Calvin Harris feat. Dua Lipa"
    assert c is True


def test_feat_dot_unchanged():
    r, c = _norm("Calvin Harris feat. Dua Lipa")
    assert c is False


def test_x_separator_normalized():
    r, c = _norm("Martin Garrix x Tiësto")
    assert r == "Martin Garrix feat. Tiësto"
    assert c is True


def test_x_in_name_not_stripped():
    # "DJ X" — X is part of a name, not a separator (no trailing space after X)
    r, c = _norm("DJ X")
    assert r == "DJ X"
    assert c is False


def test_amp_as_collab_normalized_without_mb():
    # No override, no MB — '&' treated as collaboration (use_mb=False)
    r, c = normalize_collab_artist("Calvin Harris & Dua Lipa", overrides={}, use_mb=False)
    assert r == "Calvin Harris feat. Dua Lipa"
    assert c is True


def test_amp_as_band_via_override():
    overrides = {"Bill Haley & His Comets": "band"}
    r, c = _norm("Bill Haley & His Comets", overrides=overrides)
    assert r == "Bill Haley & His Comets"
    assert c is False


def test_collab_override_forces_normalization():
    # Explicit collab override overrides any MB decision
    overrides = {"Earth, Wind & Fire": "collab"}
    r, c = normalize_collab_artist(
        "Earth, Wind & Fire", overrides=overrides, use_mb=False
    )
    assert r == "Earth, Wind feat. Fire"
    assert c is True


def test_case_insensitive_ft():
    r, c = _norm("Artist FT. Other")
    assert r == "Artist feat. Other"


def test_multiple_artists_first_x_normalized():
    r, c = _norm("A x B")
    assert r == "A feat. B"
    assert c is True


def test_no_collab_unchanged():
    r, c = _norm("Bicep")
    assert r == "Bicep"
    assert c is False


def test_custom_canonical_sep():
    r, c = normalize_collab_artist("A ft. B", canonical_sep="&", use_mb=False)
    assert r == "A & B"


def test_extra_spaces_collapsed():
    r, c = _norm("A  ft.  B")
    assert r == "A feat. B"


def test_uppercase_x_in_name_not_treated_as_separator():
    r, c = _norm("Malcolm X Foundation")
    assert r == "Malcolm X Foundation"
    assert c is False


def test_band_with_amp_plus_feat_preserved():
    overrides = {"Bill Haley & His Comets": "band"}
    r, c = normalize_collab_artist(
        "Bill Haley & His Comets ft. DJ Snake", overrides=overrides, use_mb=False
    )
    assert r == "Bill Haley & His Comets feat. DJ Snake"
    assert c is True  # ft. → feat. but & preserved


# ── collect_normalization_candidates ──────────────────────────────────────────

def _row(artist, tid="t1"):
    return {"track_id": tid, "artist": artist, "title": "Track"}


def test_collect_finds_ft_candidates():
    rows = [_row("A ft. B", "t1"), _row("C feat. D", "t2"), _row("E", "t3")]
    candidates = collect_normalization_candidates(rows, use_mb=False)
    assert len(candidates) == 1
    assert candidates[0]["original"] == "A ft. B"
    assert candidates[0]["normalized"] == "A feat. B"


def test_collect_deduplicates_same_artist():
    rows = [_row("A ft. B", "t1"), _row("A ft. B", "t2")]
    candidates = collect_normalization_candidates(rows, use_mb=False)
    # Two rows with same artist → two candidate entries (different rows)
    assert len(candidates) == 2
    assert candidates[0]["normalized"] == candidates[1]["normalized"] == "A feat. B"


def test_collect_empty_artist_skipped():
    rows = [_row(""), _row("   ")]
    candidates = collect_normalization_candidates(rows, use_mb=False)
    assert candidates == []


def test_collect_band_override_excluded():
    overrides = {"Bill Haley & His Comets": "band"}
    rows = [_row("Bill Haley & His Comets")]
    candidates = collect_normalization_candidates(rows, overrides=overrides, use_mb=False)
    assert candidates == []
