from __future__ import annotations
import re
from pathlib import Path

_ILLEGAL = r'[\/\\\:\*\?"<>\|]'

def build_final_filename(artist: str, title: str, version_info: str, key_camelot: str, bpm: str, ext: str) -> str:
    vi_raw = (version_info or "").strip()
    if vi_raw:
        # Render multiple version tokens as separate parentheses: (V1) (V2)
        parts = [p.strip() for p in vi_raw.split(",") if p.strip()]
        vi = " ".join(f"({p})" for p in parts) if parts else "(Original Mix)"
    else:
        vi = "(Original Mix)"
    k = (key_camelot or "").strip() or "??"
    b = (bpm or "").strip() or "??"
    a = (artist or "Unknown Artist").strip()
    t = (title or "Unknown Title").strip()

    # If vi already includes parentheses (multiple), do not wrap again
    name = f"{a} - {t} {vi} [{k} {b}]{ext}"
    # wyczyść nielegalne znaki w nazwie
    return re.sub(_ILLEGAL, "-", name)

def extension_for(path: Path) -> str:
    return path.suffix or ".mp3"


def parse_from_filename(path: Path) -> tuple[str, str, str]:
    """Próbuje wyciągnąć (artist, title, version_info) z nazwy pliku.
    Rozszerzone warianty:
    Artist - Title (Remix) (Extended Edit)
    Artist - Title (Karibu Remix)(Extended Edit)
    Artist - Title (Karibu Remix) (VIP Mix)
    Zwraca wszystkie kolejne nawiasy scalone w jedną wersję po przecinku.
    Jeśli nie znajdzie artysty — fallback: ("", <basename>, "")."""
    name = path.stem

    # 1) wstępne czyszczenie nazwy pliku
    # - zamień podkreślenia na spacje
    # - usuń śmieciowe wstawki w nawiasach zawierające URL/domene (np. (www.mp3vip.org))
    # - skondensuj spacje
    cleaned = name.replace("_", " ")
    # usuń ( ... ) jeśli wygląda jak adres/url lub domena
    cleaned = re.sub(r"\((?:https?://|www\.|[^)]*\.(?:com|net|org|ru|pl|de|uk|fr|it|es|cz|sk|nl|be|info|biz|xyz|site|club|music|fm|to|ua|co|io|me)\b)[^)]*\)", "", cleaned, flags=re.IGNORECASE)
    # usuń podwójne spacje i spacje wokół myślników
    cleaned = re.sub(r"\s*-[\-–—]\s*", " - ", cleaned)  # normalizuj łącznik
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    # 2) próba dopasowania z wieloma nawiasami: Artist - Title (V1) (V2) ...
    m_multi = re.match(r"^\s*(.+?)\s*-\s*(.+?)\s*(\(.+\))\s*$", cleaned)
    if m_multi:
        a, t, tail = m_multi.groups()
        # wyciągnij wszystkie grupy nawiasów
        parts = re.findall(r"\(([^)]+)\)", tail)
        version_combined = ", ".join(p.strip() for p in parts if p.strip())
        return a.strip(), t.strip(), version_combined.strip()
    # 3) próba dopasowania: Artist - Title
    m2 = re.match(r"^\s*(.+?)\s*-\s*(.+?)\s*$", cleaned)
    if m2:
        a, t = (m2.group(1).strip(), m2.group(2).strip())
        # 3a) heurystyka: jeśli tytuł kończy się znanym określeniem wersji – wydziel je
        version_markers = [
            "original mix", "extended mix", "club mix", "radio edit", "edit", "remix",
            "dub mix", "instrumental", "vip mix", "vip", "bootleg", "refix", "rework",
            "re-edit", "remaster", "club edit", "extended", "mix"
        ]
        tl = t.lower()
        found = None
        for vm in sorted(version_markers, key=len, reverse=True):
            if tl.endswith(" " + vm) or tl == vm:
                found = vm
                break
        if found:
            # wytnij wersję z końca tytułu
            base = t[: len(t) - len(found)].rstrip()
            # usuń separatory typu '-'/'–' na końcu jeśli zostały
            base = re.sub(r"[\s\-–—]+$", "", base).strip()
            return a, base or t, found.title()
        return a, t, ""
    # 4) fallback – użyj wyczyszczonej nazwy jako tytułu
    return "", cleaned.strip(), ""
