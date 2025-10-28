from __future__ import annotations

def guess_bucket(artist: str, title: str, bpm: str, genre: str, comment: str):
    """
    Zwraca tuple (ai_guess_bucket, ai_guess_comment)
    Celem jest tylko PODPOWIEDŹ. Decyzję finalną ustawiasz w CSV.
    """
    a = (artist or "").lower()
    t = (title or "").lower()
    g = (genre or "").lower()
    c = (comment or "").lower()

    # rock'n'roll/oldies
    if any(k in a for k in ["elvis", "beach boys", "chuck berry", "jerry lee lewis", "bill haley"]):
        return "CLASSICS_CANDIDATES", "oldies / rock'n'roll"

    # polski singalong / klasyki
    if any(k in a for k in ["perfect", "bajm", "lady pank", "kombi", "kombii", "łzy", "lzy", "kult", "podsiad", "podsiadło", "podsiadlo", "sanah", "myslovitz", "wilki", "zalewski"]):
        return "OPEN_FORMAT_CANDIDATES", "polski singalong/classic vibe"

    # urban / r&b / rap
    if any(x in g for x in ["hip hop", "hip-hop", "rap", "r&b", "rnb", "trap"]):
        return "OPEN_FORMAT_CANDIDATES", "urban / rnb / hiphop"

    # latin/reggaeton
    if any(x in g for x in ["reggaeton", "latin", "bachata"]) or "dembow" in c:
        return "OPEN_FORMAT_CANDIDATES", "latin/reggaeton"

    # afro house
    if "afro" in g or "afro" in c:
        return "CLUB_CANDIDATES", "afrohouse"

    # house/tech/melodic/techno/electro swing
    if any(x in g for x in ["house", "tech house", "tech-house", "melodic", "techno", "electro swing", "swing", "progressive", "organic"]):
        return "CLUB_CANDIDATES", "club/electronic"

    # 90s/00s nostalgia
    if any(x in a for x in ["snap", "corona", "haddaway", "darude", "sean paul", "fatboy slim", "prodigy", "bomfunk mc"]):
        return "CLASSICS_CANDIDATES", "90s/00s nostalgia"

    return "UNDECIDED", "no confident guess"
