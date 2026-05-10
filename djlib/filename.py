from __future__ import annotations
import re
from pathlib import Path

_ILLEGAL = r'[\/\\\:\*\?"<>\|]'

def _normalize_version_tokens(version_info: str) -> list[str]:
    return [p.strip() for p in (version_info or "").replace(";", ",").split(",") if p.strip()]

def merge_title_and_version(title: str, version_info: str) -> str:
    base = (title or "").strip()
    tokens = _normalize_version_tokens(version_info)
    if not tokens:
        return base

    stripped, embedded = split_title_and_version(base)
    if embedded:
        embedded_tokens = _normalize_version_tokens(embedded)
        if embedded_tokens == tokens:
            base = stripped or base

    first_token = tokens[0] if tokens else ""
    if first_token:
        pattern = re.compile(rf"\s*[-–—]\s*{re.escape(first_token)}\s*$", re.IGNORECASE)
        cleaned, count = pattern.subn("", base)
        if count:
            base = cleaned.strip() or base

    suffix = " ".join(f"({tok})" for tok in tokens)
    return (f"{base} {suffix}").strip()

def split_title_and_version(full_title: str) -> tuple[str, str]:
    """
    Split title into base title and version info.
    
    Recognizes version info in:
    1. Parentheses/brackets: "Title (Extended Mix)" → "Title", "Extended Mix"
       BUT only if content contains version keywords (Mix, Edit, Remix, etc.)
       "(Hear Me Tonight)" is NOT a version - it's part of the title!
    2. Dash separator: "Title - Extended Mix" → "Title", "Extended Mix"
    
    For both patterns, only splits if content contains version keywords:
    Mix, Edit, Version, Remix, Dub, VIP, Bootleg, Rework, Remaster, Live, Acoustic, etc.
    
    Returns:
        tuple[str, str]: (base_title, version_info)
    """
    s = (full_title or "").strip()
    if not s:
        return "", ""
    
    # Strip "feat. XYZ" / "ft. XYZ" from the end before processing
    # These are feature credits, not version info
    feat_match = re.search(r'\s+(feat\.?|ft\.?|featuring)\s+.+$', s, re.IGNORECASE)
    if feat_match:
        s = s[:feat_match.start()].strip()
    
    # Version indicators - content must contain one of these to be considered a version
    # Case-insensitive matching
    VERSION_KEYWORDS = [
        "mix", "edit", "version", "remix", "dub", "vip",
        "bootleg", "rework", "remaster", "remastered",
        "rub", "flip", "refix", "revamp",
        "live", "acoustic", "unplugged", "instrumental", "acapella", "a capella",
        "radio", "extended", "original", "club", "single",
        "mezcla",  # Spanish for "mix"
        "dirty", "clean", "explicit",  # content/quality markers
    ]
    
    def _is_version_content(content: str) -> bool:
        """Check if parenthesis content looks like version info."""
        c = content.lower()
        return any(kw in c for kw in VERSION_KEYWORDS)
    
    def _find_outer_paren(text: str) -> tuple[str | None, str | None]:
        """Find outermost parentheses at end of string, handling nested parens.
        
        Returns:
            tuple[str | None, str | None]: (prefix_before_paren, content_inside_paren)
        """
        text = text.rstrip()
        if not text or text[-1] not in ")]}":
            return None, None
        close_char = text[-1]
        open_char = {")" : "(", "]": "[", "}": "{"}[close_char]
        # Find matching open bracket/paren
        depth = 0
        for i in range(len(text) - 1, -1, -1):
            if text[i] == close_char:
                depth += 1
            elif text[i] == open_char:
                depth -= 1
                if depth == 0:
                    return text[:i].rstrip(), text[i + 1 : -1]
        return None, None

    # First, extract ONLY version-like content from parentheses/brackets at the end
    parenthesis_tokens: list[str] = []
    while True:
        # Find outermost parentheses/brackets at the end (handles nested parens)
        prefix, content = _find_outer_paren(s)
        if prefix is None or content is None:
            break
        content = content.strip()
        if not content:
            break
        
        # Only extract if it looks like version info
        if _is_version_content(content):
            parenthesis_tokens.append(content)
            s = prefix
        else:
            # Not a version - stop extracting (keep it as part of title)
            break
    parenthesis_tokens.reverse()
    
    # Now check for dash separator with version keywords in remaining text
    # Pattern: "Title - Something Mix/Edit/Version/etc"
    dash_match = re.search(
        r'^(.+?)\s*[-–—]\s*(.+(?:Mix|Edit|Version|Remix|Dub|VIP|Bootleg|Rework|Remaster|Re-?work|Re-?master|Rub|Flip|Refix|Revamp))$',
        s,
        re.IGNORECASE
    )
    
    if dash_match:
        # Dash pattern matched - split on dash
        base_title = dash_match.group(1).strip()
        version_from_dash = dash_match.group(2).strip()
        
        # Combine dash version with parenthesis tokens
        all_version_tokens = [version_from_dash] + parenthesis_tokens
        return base_title, ", ".join(all_version_tokens)
    else:
        # No dash pattern - just use parenthesis tokens
        return s.strip(), ", ".join(parenthesis_tokens)

def build_final_filename(artist: str, title: str, version_info: str, key_camelot: str, bpm: str, ext: str) -> str:
    base_title = (title or "Unknown Title").strip()
    extracted_title, extracted_version = split_title_and_version(base_title)
    if extracted_version and not version_info:
        base_title = extracted_title or base_title
        version_info = extracted_version
    vi_raw = (version_info or "").strip()
    if vi_raw:
        parts = _normalize_version_tokens(vi_raw)
        vi = " ".join(f"({p})" for p in parts) if parts else ""
    else:
        vi = ""
    k = (key_camelot or "").strip() or "??"
    # Round BPM to integer for filename (keep precision in tags)
    bpm_str = (bpm or "").strip()
    if bpm_str and bpm_str != "??":
        try:
            b = str(round(float(bpm_str)))
        except (ValueError, TypeError):
            b = bpm_str
    else:
        b = "??"
    a = (artist or "Unknown Artist").strip()
    # Build name with optional version info (only if not empty)
    title_part = f"{base_title or 'Unknown Title'}"
    if vi:
        name = f"{a} - {title_part} {vi} [{k} {b}]{ext}"
    else:
        name = f"{a} - {title_part} [{k} {b}]{ext}"
    return re.sub(_ILLEGAL, "-", name)

def extension_for(path: Path) -> str:
    return path.suffix or ".mp3"


_PAREN_DASH_PLACEHOLDER = "\x00"


def _shield_paren_dashes(text: str) -> str:
    """Replace dashes inside balanced parentheses/brackets with a placeholder.

    This prevents ``" - "`` inside ``(Remix - Extended)`` from being treated
    as an artist/title separator during regex-based filename parsing.
    """
    result: list[str] = []
    depth = 0
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        if ch == "-" and depth > 0:
            result.append(_PAREN_DASH_PLACEHOLDER)
        else:
            result.append(ch)
    return "".join(result)


def _unshield_dashes(text: str) -> str:
    """Restore placeholder back to dashes."""
    return text.replace(_PAREN_DASH_PLACEHOLDER, "-")


def parse_from_filename(path: Path) -> tuple[str, str, str]:
    """Próbuje wyciągnąć (artist, title, version_info) z nazwy pliku.
    Rozszerzone warianty:
    Artist - Title (Remix) (Extended Edit)
    Artist - Title (Karibu Remix)(Extended Edit)
    Artist - Title (Karibu Remix) (VIP Mix)
    Zwraca wszystkie kolejne nawiasy scalone w jedną wersję po przecinku.
    Jeśli nie znajdzie artysty — fallback: ("", <basename>, "")."""
    name = path.stem

    # Strip our own [KEY BPM] suffix before parsing (e.g. "[8A 128]", "[?? ??]").
    # build_final_filename appends this; without stripping it re-enters as part of title.
    name = re.sub(r"\s*\[(?:\d{1,2}[AB]|\?\?)\s+(?:\d+|\?\?)\]\s*$", "", name)

    # 1) wstępne czyszczenie nazwy pliku
    # - zamień podkreślenia na spacje
    # - usuń śmieciowe wstawki w nawiasach zawierające URL/domene (np. (www.mp3vip.org))
    # - skondensuj spacje
    cleaned = name.replace("_", " ")
    # Normalise featuring abbreviations BEFORE any further processing.
    # "w/" and its filesystem-sanitised form "w " (from "w_") → "feat."
    # Must run before space normalisation to catch "w  " from "w_".
    cleaned = re.sub(r'\bw/\s+', 'feat. ', cleaned, flags=re.IGNORECASE)
    # "w " only when followed by a capitalised name (avoid false positives on
    # words like "saw", "new", etc.)  Pattern: word-boundary "w" followed by
    # 2+ spaces (artefact of _ replacement) then capital letter.
    cleaned = re.sub(r'\bw\s{2,}(?=[A-Z])', 'feat. ', cleaned)
    # usuń ( ... ) jeśli wygląda jak adres/url lub domena
    cleaned = re.sub(r"\((?:https?://|www\.|[^)]*\.(?:com|net|org|ru|pl|de|uk|fr|it|es|cz|sk|nl|be|info|biz|xyz|site|club|music|fm|to|ua|co|io|me)\b)[^)]*\)", "", cleaned, flags=re.IGNORECASE)
    # usuń prefiksy numerów ścieżek na początku (01-, 01., [01], (01) itp.)
    cleaned = re.sub(r"^\s*(?:\[\s*\d{1,3}\s*\]|\(\s*\d{1,3}\s*\)|\d{1,3})[\s\._\-]+", "", cleaned)
    # normalizuj spacje wokół myślników, nie ruszając zapisów typu "AC-DC"
    cleaned = re.sub(r"-\s+", " - ", cleaned)
    cleaned = re.sub(r"\s+-", " - ", cleaned)
    cleaned = re.sub(r"\s*[–—]\s*", " - ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # strip trailing commas/semicolons — junk from bad filenames (e.g. "…Remix),")
    cleaned = re.sub(r"[,;]+$", "", cleaned).strip()

    # 1b) Protect dashes inside balanced parens from being treated as separators.
    # e.g. "Unwritten (Talon Afrohouse Remix - Extended) - Natasha Bedingfield"
    # Without this, the " - " inside the parens splits into 3 segments → garbage.
    shielded = _shield_paren_dashes(cleaned)

    dash_pattern = r"\s+-\s+" if " - " in shielded else r"\s*-\s*"

    # Version keywords - content must contain one of these to be considered version info
    VERSION_KEYWORDS = [
        "mix", "edit", "version", "remix", "dub", "vip",
        "bootleg", "rework", "remaster", "remastered",
        "rub", "flip", "refix", "revamp",
        "live", "acoustic", "unplugged", "instrumental", "acapella", "a capella",
        "radio", "extended", "original", "club", "single",
        "mezcla",  # Spanish for "mix"
    ]
    
    def _is_version_content(content: str) -> bool:
        """Check if parenthesis content looks like version info."""
        c = content.lower()
        return any(kw in c for kw in VERSION_KEYWORDS)

    # 2) próba dopasowania z wieloma nawiasami: Artist - Title (V1) (V2) ... lub [V1] [V2] ...
    m_multi = re.match(rf"^\s*(.+?){dash_pattern}(.+?)\s*([\(\[\{{].+[\)\]\}}])\s*$", shielded)
    if m_multi:
        a, t, tail = (_unshield_dashes(g) for g in m_multi.groups())
        # wyciągnij wszystkie grupy nawiasów (okrągłe, kwadratowe, klamrowe)
        parts = re.findall(r"[\(\[\{]([^\)\]\}]+)[\)\]\}]", tail)
        # ONLY include parts that look like version info
        version_parts = [p.strip() for p in parts if p.strip() and _is_version_content(p)]
        # Non-version parts should be appended back to title
        title_parts = [p.strip() for p in parts if p.strip() and not _is_version_content(p)]
        
        full_title = t.strip()
        if title_parts:
            full_title = full_title + " " + " ".join(f"({p})" for p in title_parts)
        
        version_combined = ", ".join(version_parts)
        return a.strip(), full_title.strip(), version_combined.strip()

    # 2b) próba dopasowania: Artist - Title - Version (bez nawiasów, 3 segmenty)
    m_three = re.match(rf"^\s*(.+?){dash_pattern}(.+?){dash_pattern}(.+?)\s*$", shielded)
    if m_three:
        a, middle, last = (_unshield_dashes(g.strip()) for g in m_three.groups())

        # Heuristic: if middle looks like track number (e.g., "04", "1", "12"),
        # treat last as title, ignore middle
        if re.match(r"^\d{1,3}$", middle):
            return a, last, ""

        return a, middle, last

    # 3) próba dopasowania: Artist - Title
    m2 = re.match(rf"^\s*(.+?){dash_pattern}(.+?)\s*$", shielded)
    if m2:
        a, t = (_unshield_dashes(m2.group(1).strip()), _unshield_dashes(m2.group(2).strip()))
        
        # 3a) DETECT REVERSED ORDER: if first part has version keywords, swap!
        # e.g., "Alors on danse (ALLERTZ REMIX) - Stromae" → swap to "Stromae - Alors on danse (ALLERTZ REMIX)"
        first_has_version = _is_version_content(a)
        second_has_version = _is_version_content(t)
        
        # Swap if: first part has version info but second doesn't
        # This catches "Title (Remix) - Artist" pattern
        if first_has_version and not second_has_version:
            a, t = t, a  # Swap!
            # After swap, extract version from parentheses in the title
            base_t, ver = split_title_and_version(t)
            if ver:
                return a, base_t, ver
        
        # 3b) heurystyka: jeśli tytuł kończy się znanym określeniem wersji – wydziel je
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
            # usuń separatory typu "-"/"–" na końcu jeśli zostały
            base = re.sub(r"[\s\-–—]+$", "", base).strip()
            return a, base or t, found.title()
        return a, t, ""

    # 4) fallback – użyj wyczyszczonej nazwy jako tytułu
    return "", cleaned.strip(), ""
