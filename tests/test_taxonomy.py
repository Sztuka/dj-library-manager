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
    club = ["house", "HOUSE", "tech house"]
    openf = ["rnb", "RNB", "funk"]
    out = build_ready_buckets(club, openf, mixes=True)
    # deduplikacja po kluczu kanonicznym, ale zachowujemy pierwszy wariant stylistyczny
    assert out == [
        "CLUB/house",
        "CLUB/tech house",
        "OPEN FORMAT/rnb",
        "OPEN FORMAT/funk",
        "MIXES",
    ]
