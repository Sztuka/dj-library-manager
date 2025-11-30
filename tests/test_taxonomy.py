"""
Legacy taxonomy tests - these test deprecated functionality.
The taxonomy module is maintained for backward compatibility during migration.
New code should use djlib.logistics for path building and djlib.genre_canonical for genres.
"""
from djlib.taxonomy import normalize_label, build_ready_buckets

def test_normalize_label_basic():
    # zachowujemy styl użytkownika: case i podkreślenia pozostają
    assert normalize_label("tech_house") == "tech_house"
    assert normalize_label("  hip-hop  ") == "hip-hop"
    assert normalize_label("Open  Format") == "Open Format"
    assert normalize_label("MIXES/") == "MIXES"  # rstrip('/')

def test_build_ready_buckets_dedup():
    from djlib.genre_canonical import CanonicalGenreResolver
    resolver = CanonicalGenreResolver()
    # Map raw genres to canonical labels
    club_raw = ["house", "HOUSE", "tech house"]
    openf_raw = ["rnb", "RNB", "funk"]
    club = [resolver.resolve(g)[1] if resolver.resolve(g) else g for g in club_raw]
    openf = [resolver.resolve(g)[1] if resolver.resolve(g) else g for g in openf_raw]
    out = build_ready_buckets(club, openf, mixes=True)
    # deduplicate by canonical key, keep first stylistic variant
    assert out == [
        "CLUB/House",
        "CLUB/Tech House",
        "OPEN FORMAT/R&B",
        "OPEN FORMAT/Funk",
        "MIXES",
    ]
