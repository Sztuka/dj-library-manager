#!/usr/bin/env python3
from djlib.enrich import derive_local_metadata
from djlib.metadata.beatport import search_track
from pathlib import Path

mock_tags = {
    "artist": "Salome De Bahia",
    "title": "Outro Lugar (Bob Sinclar Extended Mix)",
    "version_info": "",
    "duration": "5:17"
}

path = Path("/fake/Salome De Bahia - Outro Lugar (Bob Sinclar Extended Mix) [11A 123].mp3")
artist, title, version = derive_local_metadata(path, mock_tags)

print("derive_local_metadata result:")
print(f"  artist: {artist!r}")
print(f"  title: {title!r}")
print(f"  version: {version!r}")
print()

print(f"Beatport search with title={title!r}:")
result = search_track(artist, title, duration_s=317)
genre = result.get('genre') if result else 'NOT FOUND'
print(f"  genre: {genre}")
