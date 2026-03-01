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


# ── Paren-dash shielding: dashes inside parens are NOT field separators ──────


def test_parse_dash_inside_parens_correct_order():
    """Correct order: Artist - Title (Remix - Extended)."""
    a, t, v = parse_from_filename(
        Path("Natasha Bedingfield - Unwritten (Talon Afrohouse Remix - Extended).wav")
    )
    assert a == "Natasha Bedingfield"
    assert t == "Unwritten"
    assert v == "Talon Afrohouse Remix - Extended"


def test_parse_dash_inside_parens_reversed_order():
    """Reversed order: Title (Remix - Extended) - Artist.

    The parser should auto-detect the swap (first part has version keywords)
    and return the correct artist/title/version.
    """
    a, t, v = parse_from_filename(
        Path("Unwritten (Talon Afrohouse Remix - Extended) - Natasha Bedingfield.wav")
    )
    assert a == "Natasha Bedingfield"
    assert t == "Unwritten"
    assert v == "Talon Afrohouse Remix - Extended"


def test_parse_multiple_dashes_inside_parens():
    """Multiple dashes inside parens should all be preserved."""
    a, t, v = parse_from_filename(
        Path("Artist - Track (Remix - Extended - Club Version).mp3")
    )
    assert a == "Artist"
    assert t == "Track"
    assert v == "Remix - Extended - Club Version"


def test_parse_reversed_simple_remix():
    """Title (Remix) - Artist swap detection (no dash inside parens)."""
    a, t, v = parse_from_filename(
        Path("Alors on danse (ALLERTZ REMIX) - Stromae.wav")
    )
    assert a == "Stromae"
    assert t == "Alors on danse"
    assert v == "ALLERTZ REMIX"


# ── Existing merge tests ────────────────────────────────────────────────────


def test_merge_title_wraps_version_in_parentheses():
    result = merge_title_and_version("Pompeii", "Merchant vs Vidojean & Oliver Loenn City Boys Edit")
    assert result == "Pompeii (Merchant vs Vidojean & Oliver Loenn City Boys Edit)"


def test_merge_title_normalizes_hyphen_suffix():
    base = "Pompeii - Merchant vs Vidojean & Oliver Loenn City Boys Edit"
    version = "Merchant vs Vidojean & Oliver Loenn City Boys Edit"
    result = merge_title_and_version(base, version)
    assert result == "Pompeii (Merchant vs Vidojean & Oliver Loenn City Boys Edit)"


# ── w_ / w/ → feat. normalisation ──────────────────────────────────────────


def test_parse_w_underscore_as_feat():
    """'w_' in filename (sanitised 'w/') should become 'feat.' in title."""
    _, title, _ = parse_from_filename(Path("september maru w_ Dave Nunes.mp3"))
    assert "feat." in title.lower()
    assert "Dave Nunes" in title


def test_parse_w_underscore_no_false_positive():
    """'w_' should NOT trigger when not followed by a capital letter."""
    _, title, _ = parse_from_filename(Path("new_world_order.mp3"))
    assert "feat" not in title.lower()
