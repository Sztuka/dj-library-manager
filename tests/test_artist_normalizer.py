"""Tests for djlib.artist_normalizer — clustering, fingerprinting, alias I/O."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from djlib.artist_normalizer import (
    _cluster_fingerprint,
    _normalize_key,
    _pick_canonical,
    _split_compound,
    cluster_artists,
    collect_artists,
    dismiss_cluster,
    load_aliases,
    promote_pending_to_canonical,
    save_aliases,
    write_pending_entry,
)
from djlib.library_schema import merge_with_existing_library


# ── _split_compound ────────────────────────────────────────────────────────────

def test_split_compound_feat():
    assert _split_compound("Calvin Harris feat. Dua Lipa") == ["Calvin Harris", "Dua Lipa"]

def test_split_compound_ft():
    assert _split_compound("ZHU ft. Trombone Shorty") == ["ZHU", "Trombone Shorty"]

def test_split_compound_ampersand():
    assert _split_compound("Calvin Harris & Dua Lipa") == ["Calvin Harris", "Dua Lipa"]

def test_split_compound_x():
    assert _split_compound("Disclosure x Sam Smith") == ["Disclosure", "Sam Smith"]

def test_split_compound_band_name_unchanged():
    # Single atom — no split marker
    assert _split_compound("Bill Haley & His Comets") == ["Bill Haley", "His Comets"]

def test_split_compound_single_artist():
    assert _split_compound("The Tremeloes") == ["The Tremeloes"]

def test_split_compound_empty():
    assert _split_compound("") == []


# ── _normalize_key ─────────────────────────────────────────────────────────────

def test_normalize_key_lowercases():
    assert _normalize_key("ZHU") == "zhu"

def test_normalize_key_strips():
    assert _normalize_key("  The Tremeloes  ") == "the tremeloes"

def test_normalize_key_nfc():
    # NFC vs NFD — both should normalize to the same key
    import unicodedata
    nfd = unicodedata.normalize("NFD", "Björk")
    nfc = unicodedata.normalize("NFC", "Björk")
    assert _normalize_key(nfd) == _normalize_key(nfc)


# ── _cluster_fingerprint ───────────────────────────────────────────────────────

def test_fingerprint_is_order_independent():
    fp1 = _cluster_fingerprint(["Tremeloes", "The Tremeloes"])
    fp2 = _cluster_fingerprint(["The Tremeloes", "Tremeloes"])
    assert fp1 == fp2

def test_fingerprint_is_stable():
    fp = _cluster_fingerprint(["The Tremeloes", "Tremeloes"])
    assert fp == "the tremeloes|tremeloes"


# ── collect_artists ────────────────────────────────────────────────────────────

def test_collect_artists_deduplicates_by_normalized_name():
    rows = [
        {"artist": "ZHU", "mb_artist_id": ""},
        {"artist": "Zhu", "mb_artist_id": ""},  # same after normalize
    ]
    artists = collect_artists(rows, [])
    names = [a for a, _ in artists]
    assert len(names) == 1
    assert names[0] == "ZHU"  # first-seen wins

def test_collect_artists_splits_compound():
    rows = [{"artist": "Calvin Harris feat. Dua Lipa", "mb_artist_id": ""}]
    artists = collect_artists(rows, [])
    names = [a for a, _ in artists]
    assert "Calvin Harris" in names
    assert "Dua Lipa" in names

def test_collect_artists_skips_empty():
    rows = [{"artist": "", "mb_artist_id": ""}, {"artist": "ZHU", "mb_artist_id": ""}]
    artists = collect_artists(rows, [])
    assert len(artists) == 1

def test_collect_artists_merges_library_and_unsorted():
    lib = [{"artist": "The Tremeloes", "mb_artist_id": ""}]
    unsorted = [{"artist": "Tremeloes", "mb_artist_id": ""}]
    artists = collect_artists(lib, unsorted)
    names = [a for a, _ in artists]
    assert "The Tremeloes" in names
    assert "Tremeloes" in names


# ── cluster_artists ────────────────────────────────────────────────────────────

def _make_aliases():
    return {"canonical": {}, "dismissed": [], "pending": {}}

def test_cluster_finds_the_tremeloes():
    rows = [
        {"artist": "The Tremeloes", "mb_artist_id": ""},
        {"artist": "Tremeloes", "mb_artist_id": ""},
    ]
    artists = collect_artists(rows, [])
    clusters = cluster_artists(artists, _make_aliases(), threshold=70)
    assert len(clusters) == 1
    assert set(clusters[0]["members"]) == {"The Tremeloes", "Tremeloes"}
    assert clusters[0]["confidence"] >= 70

def test_cluster_mbz_id_match_is_100_percent():
    rows = [
        {"artist": "The Tremeloes", "mb_artist_id": "abc-123"},
        {"artist": "Tremeloes",     "mb_artist_id": "abc-123"},
    ]
    artists = collect_artists(rows, [])
    clusters = cluster_artists(artists, _make_aliases(), threshold=70)
    assert len(clusters) == 1
    assert clusters[0]["method"] == "mbz"
    assert clusters[0]["confidence"] == 100

def test_cluster_skips_dismissed():
    rows = [
        {"artist": "The Tremeloes", "mb_artist_id": ""},
        {"artist": "Tremeloes", "mb_artist_id": ""},
    ]
    artists = collect_artists(rows, [])
    fp = _cluster_fingerprint(["The Tremeloes", "Tremeloes"])
    aliases = {"canonical": {}, "dismissed": [fp], "pending": {}}
    clusters = cluster_artists(artists, aliases, threshold=70, show_dismissed=False)
    assert len(clusters) == 0

def test_cluster_shows_dismissed_when_requested():
    rows = [
        {"artist": "The Tremeloes", "mb_artist_id": ""},
        {"artist": "Tremeloes", "mb_artist_id": ""},
    ]
    artists = collect_artists(rows, [])
    fp = _cluster_fingerprint(["The Tremeloes", "Tremeloes"])
    aliases = {"canonical": {}, "dismissed": [fp], "pending": {}}
    clusters = cluster_artists(artists, aliases, threshold=70, show_dismissed=True)
    assert len(clusters) == 1

def test_cluster_does_not_merge_unrelated_artists():
    rows = [
        {"artist": "ZHU", "mb_artist_id": ""},
        {"artist": "Disclosure", "mb_artist_id": ""},
    ]
    artists = collect_artists(rows, [])
    clusters = cluster_artists(artists, _make_aliases(), threshold=70)
    assert len(clusters) == 0

def test_cluster_prefills_canonical_from_existing_map():
    rows = [
        {"artist": "The Tremeloes", "mb_artist_id": ""},
        {"artist": "Tremeloes", "mb_artist_id": ""},
    ]
    artists = collect_artists(rows, [])
    aliases = {"canonical": {"Tremeloes": "The Tremeloes"}, "dismissed": [], "pending": {}}
    clusters = cluster_artists(artists, aliases, threshold=70)
    assert clusters[0]["canonical"] == "The Tremeloes"


# ── alias YAML I/O ─────────────────────────────────────────────────────────────

def test_load_aliases_returns_empty_structure_when_absent():
    aliases = load_aliases(Path("/tmp/does_not_exist_artist_aliases.yml"))
    assert aliases == {"canonical": {}, "dismissed": [], "pending": {}}

def test_save_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "artist_aliases.yml"
        data = {
            "canonical": {"Tremeloes": "The Tremeloes"},
            "dismissed": ["the tremeloes|tremeloes"],
            "pending": {},
        }
        save_aliases(path, data)
        loaded = load_aliases(path)
        assert loaded["canonical"] == data["canonical"]
        assert loaded["dismissed"] == data["dismissed"]

def test_write_pending_then_promote():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "artist_aliases.yml"
        fp = _cluster_fingerprint(["The Tremeloes", "Tremeloes"])
        write_pending_entry(path, fp, "The Tremeloes", ["Tremeloes", "The Tremeloes"])
        data = load_aliases(path)
        assert fp in data["pending"]
        assert data["pending"][fp]["canonical"] == "The Tremeloes"

        promote_pending_to_canonical(path, fp, "The Tremeloes", ["Tremeloes", "The Tremeloes"])
        data = load_aliases(path)
        assert fp not in data["pending"]
        assert data["canonical"].get("Tremeloes") == "The Tremeloes"
        assert data["canonical"].get("The Tremeloes") == "The Tremeloes"

def test_dismiss_cluster_adds_fingerprint():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "artist_aliases.yml"
        dismiss_cluster(path, ["The Tremeloes", "Tremeloes"])
        data = load_aliases(path)
        fp = _cluster_fingerprint(["The Tremeloes", "Tremeloes"])
        assert fp in data["dismissed"]

def test_dismiss_cluster_idempotent():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "artist_aliases.yml"
        dismiss_cluster(path, ["The Tremeloes", "Tremeloes"])
        dismiss_cluster(path, ["The Tremeloes", "Tremeloes"])
        data = load_aliases(path)
        fp = _cluster_fingerprint(["The Tremeloes", "Tremeloes"])
        assert data["dismissed"].count(fp) == 1


# ── sync protection via library_schema ────────────────────────────────────────

def test_normalized_artist_survives_sync():
    """When artist_normalized=yes, sync must not overwrite artist with DJ-software value."""
    existing = [{
        "track_id": "tid-1",
        "artist": "The Tremeloes",
        "artist_normalized": "yes",
        "file_hash": "abc",
        "original_path": "/music/track.mp3",
        "added_date": "2025-01-01",
        "key_original": "",
        "key_source": "",
        "analysis_source": "",
        "field_sources": "",
        "duplicate_paths": "",
        "live_location": "",
        "live_path": "",
        "mb_artist_id": "mbz-123",
    }]
    new_from_dj_software = [{
        "track_id": "tid-1",
        "artist": "Tremeloes",  # stale name from Rekordbox
        "artist_normalized": "",
        "file_hash": "",
        "original_path": "",
        "added_date": "",
        "key_original": "",
        "key_source": "",
        "analysis_source": "",
        "field_sources": "",
        "duplicate_paths": "",
        "live_location": "",
        "live_path": "",
        "mb_artist_id": "",
    }]
    merged = merge_with_existing_library(new_from_dj_software, existing)
    assert len(merged) == 1
    assert merged[0]["artist"] == "The Tremeloes"
    assert merged[0]["artist_normalized"] == "yes"

def test_unnormalized_artist_is_overwritten_by_sync():
    """Tracks without artist_normalized=yes should still get DJ-software artist on sync."""
    existing = [{
        "track_id": "tid-2",
        "artist": "Old Name",
        "artist_normalized": "",
        "file_hash": "xyz",
        "original_path": "/music/track2.mp3",
        "added_date": "2025-01-01",
        "key_original": "",
        "key_source": "",
        "analysis_source": "",
        "field_sources": "",
        "duplicate_paths": "",
        "live_location": "",
        "live_path": "",
        "mb_artist_id": "",
    }]
    new_from_dj_software = [{
        "track_id": "tid-2",
        "artist": "New Name From Rekordbox",
        "artist_normalized": "",
        "file_hash": "",
        "original_path": "",
        "added_date": "",
        "key_original": "",
        "key_source": "",
        "analysis_source": "",
        "field_sources": "",
        "duplicate_paths": "",
        "live_location": "",
        "live_path": "",
        "mb_artist_id": "",
    }]
    merged = merge_with_existing_library(new_from_dj_software, existing)
    assert merged[0]["artist"] == "New Name From Rekordbox"
