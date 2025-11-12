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
try:
    from djlib.bucketing.rules import RulesBucketAssigner
    rules_assigner = RulesBucketAssigner()
except ImportError:
    rules_assigner = None
try:
    from djlib.audio.essentia_backend import analyze as analyze_audio  # optional
except Exception:
    analyze_audio = None  # type: ignore


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
        # detected audio metrics (optional)
        "bpm_detected", "bpm_confidence", "bpm_correction",
        "key_detected_camelot", "key_strength",
        "energy_score",
        # additional audio features for genre classification
        "zero_crossing_rate", "danceability", "chords_changes_rate", "tuning_diatonic_strength",
        "mfcc_0", "mfcc_1", "mfcc_2", "mfcc_3", "mfcc_4", "mfcc_5", "mfcc_6", "mfcc_7", "mfcc_8", "mfcc_9", "mfcc_10", "mfcc_11", "mfcc_12",
        "chroma_0", "chroma_1", "chroma_2", "chroma_3", "chroma_4", "chroma_5", "chroma_6", "chroma_7", "chroma_8", "chroma_9", "chroma_10", "chroma_11",
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

            # audio analysis (optional, if backend available)
            bpm_det = bpm_conf = bpm_corr = ""
            key_det = key_strength = ""
            energy_score = ""
            zero_crossing_rate = danceability = chords_changes_rate = tuning_diatonic_strength = ""
            mfcc_coeffs = [""] * 13
            chroma_coeffs = [""] * 12
            
            if analyze_audio:
                try:
                    ares = analyze_audio(p)
                    v = ares.get("bpm")
                    if v is not None:
                        bpm_det = str(v)
                    v = ares.get("bpm_conf")
                    if v is not None:
                        try:
                            bpm_conf = f"{float(v):.2f}"
                        except Exception:
                            bpm_conf = str(v)
                    v = ares.get("bpm_corr")
                    if v is not None:
                        bpm_corr = str(v)
                    if ares.get("key_camelot"):
                        key_det = str(ares.get("key_camelot"))
                    v = ares.get("key_strength")
                    if v is not None:
                        try:
                            key_strength = f"{float(v):.2f}"
                        except Exception:
                            key_strength = str(v)
                    v = ares.get("energy")
                    if v is not None:
                        try:
                            energy_score = f"{float(v):.3f}"
                        except Exception:
                            energy_score = str(v)
                    
                    # Additional genre features
                    v = ares.get("zero_crossing_rate")
                    if v is not None:
                        zero_crossing_rate = f"{float(v):.4f}"
                    v = ares.get("danceability")
                    if v is not None:
                        danceability = f"{float(v):.4f}"
                    v = ares.get("chords_changes_rate")
                    if v is not None:
                        chords_changes_rate = f"{float(v):.4f}"
                    v = ares.get("tuning_diatonic_strength")
                    if v is not None:
                        tuning_diatonic_strength = f"{float(v):.4f}"
                    
                    # MFCC coefficients
                    for i in range(13):
                        v = ares.get(f"mfcc_{i}")
                        if v is not None:
                            mfcc_coeffs[i] = f"{float(v):.4f}"
                    
                    # Chroma features
                    for i in range(12):
                        v = ares.get(f"chroma_{i}")
                        if v is not None:
                            chroma_coeffs[i] = f"{float(v):.4f}"
                            
                except Exception:
                    pass

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

            # bucket suggestion based on rules
            bucket = ""
            bucket_conf = 0.0
            if rules_assigner:
                try:
                    # Convert track data to format expected by assigner
                    track_data = {
                        'filename': p.name,
                        'tag_genre': (tags.get("genre") or "").strip(),
                        'genre_main': g_main,
                        'genre_sub1': g_sub1,
                        'genre_sub2': g_sub2,
                        'bpm_detected': bpm_det,
                        'key_detected_camelot': key_det,
                        'energy_score': energy_score,
                    }
                    bucket, bucket_conf = rules_assigner.predict(track_data)
                except Exception as e:
                    print(f"Bucket assignment failed for {p.name}: {e}")
            else:
                # Fallback to old method
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
                # detected audio metrics
                "bpm_detected": bpm_det,
                "bpm_confidence": bpm_conf,
                "bpm_correction": bpm_corr,
                "key_detected_camelot": key_det,
                "key_strength": key_strength,
                "energy_score": energy_score,
                # additional audio features
                "zero_crossing_rate": zero_crossing_rate,
                "danceability": danceability,
                "chords_changes_rate": chords_changes_rate,
                "tuning_diatonic_strength": tuning_diatonic_strength,
                **{f"mfcc_{i}": mfcc_coeffs[i] for i in range(13)},
                **{f"chroma_{i}": chroma_coeffs[i] for i in range(12)},
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
