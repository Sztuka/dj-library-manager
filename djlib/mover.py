from __future__ import annotations
from pathlib import Path
import shutil
from datetime import datetime

from djlib.taxonomy import target_to_path, ensure_taxonomy_folders

def resolve_target_path(target: str) -> Path | None:
    p = target_to_path(target)
    if p:
        p.mkdir(parents=True, exist_ok=True)
    return p

def move_with_rename(src: Path, dest_dir: Path, final_name: str) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / final_name
    if dest.exists():
        stem = dest.stem
        ext = dest.suffix
        i = 2
        while True:
            cand = dest_dir / f"{stem} ({i}){ext}"
            if not cand.exists():
                dest = cand
                break
            i += 1
    shutil.move(str(src), str(dest))
    return dest

def utc_now_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
