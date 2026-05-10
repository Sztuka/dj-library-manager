from __future__ import annotations
from pathlib import Path
import shutil
from datetime import datetime, timezone

# Logistics-only path building
from djlib.logistics import get_destination_path


def resolve_target_path(target: str) -> Path | None:
    """Resolve target path using logistics destinations only.
    
    Supported destinations:
        - "library" → Music Library root (artist subfolders)
        - "reject" → DJ Reject folder
        - "mixes" → MIXES subfolder (flat structure)

    Returns:
        Path to destination directory, or None if target is invalid
    """
    target_lower = (target or "").lower().strip()

    if target_lower in ("library", "reject", "mixes"):
        p = get_destination_path(target_lower)  # type: ignore[arg-type]
        if p:
            p.mkdir(parents=True, exist_ok=True)
        return p
    
    return None

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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
