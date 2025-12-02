from pathlib import Path

from djlib.filename import parse_from_filename, merge_title_and_version


def test_parse_preserves_hyphenated_artist():
    artist, title, version = parse_from_filename(Path("AC-DC - T.N.T. [12B 128].mp3"))
    assert artist == "AC-DC"
    assert title == "T.N.T. [12B 128]"
    assert version == ""


def test_parse_artist_title_version_pattern():
    artist, title, version = parse_from_filename(Path("Justice - Phantom - Original Mix.mp3"))
    assert artist == "Justice"
    assert title == "Phantom"
    assert version == "Original Mix"


def test_merge_title_wraps_version_in_parentheses():
    result = merge_title_and_version("Pompeii", "Merchant vs Vidojean & Oliver Loenn City Boys Edit")
    assert result == "Pompeii (Merchant vs Vidojean & Oliver Loenn City Boys Edit)"


def test_merge_title_normalizes_hyphen_suffix():
    base = "Pompeii - Merchant vs Vidojean & Oliver Loenn City Boys Edit"
    version = "Merchant vs Vidojean & Oliver Loenn City Boys Edit"
    result = merge_title_and_version(base, version)
    assert result == "Pompeii (Merchant vs Vidojean & Oliver Loenn City Boys Edit)"
