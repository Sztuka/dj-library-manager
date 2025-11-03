#!/usr/bin/env python3
from __future__ import annotations
import csv
from pathlib import Path
from typing import Dict
import sys

# Use project modules
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from djlib.config import INBOX_DIR, LIB_ROOT, AUDIO_EXTS
from djlib.tags import read_tags
from djlib.filename import parse_from_filename
from djlib.enrich import suggest_metadata
from djlib.metadata.genre_resolver import resolve as resolve_genres
from djlib.genre import external_genre_votes, load_taxonomy_map, suggest_bucket_from_votes


def _duration_from_tags_or_file(p: Path, tags: Dict[str, str]) -> str:
    d = (tags.get("duration") or "").strip()
    if d:
        return d
    # fallback: compute seconds -> mm:ss via mutagen if available
    try:
        from mutagen import File as MutFile  # type: ignore
        f = MutFile(str(p))
        if f is not None and getattr(f, "info", None) and getattr(f.info, "length", None):
            sec = int(round(float(f.info.length)))
            m, s = divmod(sec, 60)
            return f"{m}:{s:02d}"
    except Exception:
        pass
    return ""


def main() -> int:
    inbox = Path(INBOX_DIR)
    out_path = Path(LIB_ROOT) / "preview_inbox.csv"
    files = [p for p in sorted(inbox.iterdir()) if p.is_file() and p.suffix.lower() in AUDIO_EXTS]

    # load taxonomy mapping for bucket suggest
    tag_map = load_taxonomy_map()

    cols = [
        # original
        "filename",
        "tag_artist", "tag_title", "tag_genre", "tag_key_camelot", "tag_bpm", "tag_duration",
        # parsed
        "parsed_artist", "parsed_title", "parsed_version",
        # suggested (basic)
        "suggest_artist", "suggest_title",
        # suggested genres (3)
        "genre_main", "genre_sub1", "genre_sub2",
        # bucket
        "bucket_suggest", "bucket_confidence",
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for p in files:
            try:
                tags = read_tags(p)
            except Exception:
                tags = {}
            parsed_artist, parsed_title, parsed_version = parse_from_filename(p)

            # base suggest for artist/title via enrich (genre from resolver separately)
            s = suggest_metadata(p, tags)
            sugg_artist = s.get("artist_suggest", "")
            sugg_title  = s.get("title_suggest", "")
            # Show version in the title (no separate tag in many players)
            ver = (s.get("version_suggest") or parsed_version or "").strip()
            base_title = sugg_title or parsed_title
            if ver and base_title and f"({ver})" not in base_title:
                sugg_title = f"{base_title} ({ver})"
            else:
                sugg_title = base_title

            # genres via resolver (MB + Last.fm + Spotify)
            gr = None
            try:
                dur_s = None
                d = (tags.get("duration") or "").strip()
                if d and ":" in d:
                    m, ssec = d.split(":", 1)
                    dur_s = int(m) * 60 + int(ssec)
                gr = resolve_genres(sugg_artist or parsed_artist, sugg_title or parsed_title, duration_s=dur_s)
            except Exception:
                gr = None
            g_main = gr.main if gr else ""
            g_sub1 = gr.subs[0] if (gr and gr.subs) else ""
            g_sub2 = gr.subs[1] if (gr and len(gr.subs) > 1) else ""

            # bucket suggestion based on external votes and taxonomy map
            bucket = ""
            bucket_conf = 0.0
            try:
                votes = external_genre_votes(sugg_artist or parsed_artist, sugg_title or parsed_title)
                if votes:
                    b, conf, _ = suggest_bucket_from_votes(votes, tag_map)
                    bucket, bucket_conf = b, conf
            except Exception:
                pass

            row = {
                "filename": p.name,
                "tag_artist": (tags.get("artist") or "").strip(),
                "tag_title": (tags.get("title") or "").strip(),
                "tag_genre": (tags.get("genre") or "").strip(),
                "tag_key_camelot": (tags.get("key_camelot") or "").strip(),
                "tag_bpm": (tags.get("bpm") or "").strip(),
                "tag_duration": _duration_from_tags_or_file(p, tags),
                "parsed_artist": parsed_artist,
                "parsed_title": parsed_title,
                "parsed_version": parsed_version,
                "suggest_artist": sugg_artist,
                "suggest_title": sugg_title,
                "genre_main": g_main,
                "genre_sub1": g_sub1,
                "genre_sub2": g_sub2,
                "bucket_suggest": bucket,
                "bucket_confidence": f"{bucket_conf:.2f}" if bucket_conf else "",
            }
            w.writerow(row)

    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
