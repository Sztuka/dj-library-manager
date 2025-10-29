from djlib.webapp import normalize_label, build_ready_buckets

def test_normalize_label_basic():
    assert normalize_label("tech_house") == "TECH HOUSE"
    assert normalize_label("  hip-hop  ") == "HIP-HOP"
    assert normalize_label("Open  Format") == "OPEN FORMAT"
    assert normalize_label("MIXES/") == "MIXES"  # rstrip('/')

def test_build_ready_buckets_dedup():
    club = ["house", "HOUSE", "tech house"]
    openf = ["party dance", "PARTY DANCE", "funk  soul"]
    out = build_ready_buckets(club, openf, mixes=True)
    assert out == [
        "CLUB/HOUSE",
        "CLUB/TECH HOUSE",
        "OPEN FORMAT/PARTY DANCE",
        "OPEN FORMAT/FUNK SOUL",
        "MIXES",
    ]
