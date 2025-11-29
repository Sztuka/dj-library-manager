from __future__ import annotations
from pathlib import Path
import shutil
from datetime import datetime, timezone

# Legacy taxonomy support (deprecated)
try:
    from djlib.taxonomy import target_to_path, ensure_taxonomy_folders
    _LEGACY_TAXONOMY_AVAILABLE = True
except ImportError:
    _LEGACY_TAXONOMY_AVAILABLE = False
    target_to_path = None  # type: ignore
    ensure_taxonomy_folders = None  # type: ignore

# New logistics-only path building
from djlib.logistics import build_library_path, build_reject_path, build_archive_path


def resolve_target_path(target: str) -> Path | None:
    """Resolve target path. Supports both new logistics model and legacy taxonomy.
    
    New model (recommended):
        - "library" → LIBRARY/{Artist}/
        - "reject" → REJECT/
        - "archive" → ARCHIVE/{Artist}/
        
    Legacy model (deprecated):
        - "READY TO PLAY/CLUB/AFRO HOUSE" → old taxonomy-based path
    """
    target_lower = (target or "").lower().strip()
    
    # New logistics model
    if target_lower in ("library", "reject", "archive"):
        from djlib.logistics import get_destination_path
        p = get_destination_path(target_lower)  # type: ignore[arg-type]
        if p:
            p.mkdir(parents=True, exist_ok=True)
        return p
    
    # Legacy taxonomy path (backward compatibility)
    if _LEGACY_TAXONOMY_AVAILABLE and target_to_path:
        p = target_to_path(target)
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
