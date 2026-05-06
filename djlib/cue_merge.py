"""Cue point merging and duplicate cleanup for DJ software databases.

When a track has acoustic duplicates (same song, different path/quality), this
module merges the duplicate's cue points into the winner and removes the
duplicate from Rekordbox and Traktor.

Called from cmd_apply when a row has a non-empty duplicate_paths field.
"""
from __future__ import annotations

import json
import logging
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

try:
    from pyrekordbox import Rekordbox6Database
    from pyrekordbox.db6 import tables as rb_tables
    PYREKORDBOX_AVAILABLE = True
except ImportError:
    PYREKORDBOX_AVAILABLE = False

try:
    from traktor_nml_utils import TraktorCollection
    TRAKTOR_UTILS_AVAILABLE = True
except ImportError:
    TRAKTOR_UTILS_AVAILABLE = False


def parse_duplicate_paths(raw: str) -> List[str]:
    """Parse the duplicate_paths CSV field (JSON array) into a list of paths."""
    if not raw or not raw.strip():
        return []
    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return [str(p) for p in result if p]
    except (json.JSONDecodeError, ValueError):
        log.warning("duplicate_paths contains invalid JSON: %r", raw[:80])
    return []


def _nfc(path: str) -> str:
    return unicodedata.normalize("NFC", path)


# ── Rekordbox ──────────────────────────────────────────────────────────────────

def _rb_find_content(db: Any, file_path: str) -> Optional[Any]:
    """Find a DjmdContent row by file path (FolderPath attribute)."""
    path_nfc = _nfc(file_path)
    for content in db.get_content():
        fp = _nfc(getattr(content, "FolderPath", "") or "")
        if fp == path_nfc:
            return content
    return None


def merge_rekordbox_cues(db: Any, winner_path: str, dup_path: str) -> bool:
    """Copy cues from dup_path's Rekordbox entry into winner_path's entry.

    Returns True on success/no-op, False on unexpected error.
    Caller is responsible for db.commit() after processing all duplicates.
    """
    dup_content = _rb_find_content(db, dup_path)
    if dup_content is None:
        log.debug("Duplicate not in Rekordbox, skipping cue merge: %s", dup_path)
        return True

    winner_content = _rb_find_content(db, winner_path)
    if winner_content is None:
        log.warning("Winner not found in Rekordbox DB: %s", winner_path)
        return False

    try:
        dup_cues = list(dup_content.Cues or [])
        winner_cues = list(winner_content.Cues or [])
    except Exception as exc:
        log.warning("Could not read cues from Rekordbox: %s", exc)
        return False

    # Existing winner positions: (InMsec, Kind) uniquely identifies a cue slot
    winner_positions = {(c.InMsec, c.Kind) for c in winner_cues}
    winner_hotcue_slots = {c.Kind for c in winner_cues if c.Kind and c.Kind > 0}

    merged = 0
    for cue in dup_cues:
        key = (cue.InMsec, cue.Kind)
        if key in winner_positions:
            continue
        # Don't overwrite an occupied hotcue slot at a different position
        if cue.Kind and cue.Kind > 0 and cue.Kind in winner_hotcue_slots:
            log.debug("Hotcue slot %d already occupied in winner, skipping", cue.Kind)
            continue
        try:
            new_id = str(db.generate_unused_id(rb_tables.DjmdCue))
            new_cue = rb_tables.DjmdCue(
                ID=new_id,
                ContentID=winner_content.ID,
                InMsec=cue.InMsec,
                InFrame=cue.InFrame,
                InMpegFrame=cue.InMpegFrame,
                InMpegAbs=cue.InMpegAbs,
                OutMsec=cue.OutMsec,
                OutFrame=cue.OutFrame,
                OutMpegFrame=cue.OutMpegFrame,
                OutMpegAbs=cue.OutMpegAbs,
                Kind=cue.Kind,
                Color=cue.Color,
                ColorTableIndex=cue.ColorTableIndex,
                ActiveLoop=cue.ActiveLoop,
                Comment=cue.Comment,
                BeatLoopSize=cue.BeatLoopSize,
                CueMicrosec=cue.CueMicrosec,
                InPointSeekInfo=cue.InPointSeekInfo,
                OutPointSeekInfo=cue.OutPointSeekInfo,
            )
            db.add(new_cue)
            winner_positions.add(key)
            if cue.Kind and cue.Kind > 0:
                winner_hotcue_slots.add(cue.Kind)
            merged += 1
        except Exception as exc:
            log.warning("Failed to add cue (InMsec=%s, Kind=%s): %s", cue.InMsec, cue.Kind, exc)

    log.info("Rekordbox: merged %d cue(s) from %s into %s", merged, Path(dup_path).name, Path(winner_path).name)
    return True


def remove_rekordbox_track(db: Any, dup_path: str) -> bool:
    """Delete a track entry from Rekordbox DB (cascades to its cues).

    Returns True on success/no-op, False on error.
    """
    content = _rb_find_content(db, dup_path)
    if content is None:
        log.debug("Duplicate not in Rekordbox (already gone): %s", dup_path)
        return True
    try:
        db.delete(content)
        log.info("Rekordbox: removed entry for %s", Path(dup_path).name)
        return True
    except Exception as exc:
        log.warning("Could not delete Rekordbox entry for %s: %s", dup_path, exc)
        return False


# ── Traktor ────────────────────────────────────────────────────────────────────

def _traktor_reconstruct_path(location: Any) -> str:
    """Reconstruct a full file path from a Traktor LOCATION XML element or object."""
    # Support both raw ET element and traktor_nml_utils model object
    if hasattr(location, "get"):
        # ET element
        dir_path = location.get("DIR", "")
        file_name = location.get("FILE", "")
        volume = location.get("VOLUME", "")
    else:
        dir_path = getattr(location, "dir", "") or ""
        file_name = getattr(location, "file", "") or ""
        volume = getattr(location, "volume", "") or ""

    parts = [p for p in dir_path.split("/:") if p]
    if volume and volume != "Macintosh HD":
        return str(Path("/Volumes") / volume / "/".join(parts) / file_name)
    return str(Path("/") / "/".join(parts) / file_name)


def _traktor_find_entry_xml(tree_root: ET.Element, file_path: str) -> Optional[ET.Element]:
    """Find a Traktor ENTRY element matching file_path using raw XML."""
    target = _nfc(file_path)
    for entry in tree_root.findall(".//ENTRY"):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        reconstructed = _nfc(_traktor_reconstruct_path(loc))
        if reconstructed == target:
            return entry
    return None


def merge_traktor_cues(winner_path: str, dup_path: str, collection_path: Path) -> bool:
    """Merge Traktor cue points from duplicate into winner entry.

    Uses raw XML (consistent with get_traktor_track_ids pattern).
    Returns True on success/no-op, False on error.
    """
    if not TRAKTOR_UTILS_AVAILABLE:
        log.debug("traktor-nml-utils not available, skipping Traktor cue merge")
        return True

    try:
        tree = ET.parse(str(collection_path))
        root = tree.getroot()
    except Exception as exc:
        log.warning("Could not parse Traktor collection: %s", exc)
        return False

    dup_entry = _traktor_find_entry_xml(root, dup_path)
    if dup_entry is None:
        log.debug("Duplicate not in Traktor collection: %s", dup_path)
        return True

    winner_entry = _traktor_find_entry_xml(root, winner_path)
    if winner_entry is None:
        log.warning("Winner not found in Traktor collection: %s", winner_path)
        return False

    dup_cues = dup_entry.findall("CUE_V2")
    if not dup_cues:
        return True

    winner_cues = winner_entry.findall("CUE_V2")
    # Key: (start_str, hotcue_str) — exact match on position string
    winner_keys = {(c.get("START", ""), c.get("HOTCUE", "")) for c in winner_cues}
    winner_hotcue_slots = {c.get("HOTCUE", "") for c in winner_cues if c.get("HOTCUE", "-1") != "-1"}

    merged = 0
    for cue in dup_cues:
        start = cue.get("START", "")
        hotcue = cue.get("HOTCUE", "-1")
        key = (start, hotcue)
        if key in winner_keys:
            continue
        if hotcue != "-1" and hotcue in winner_hotcue_slots:
            log.debug("Traktor hotcue slot %s already occupied, skipping", hotcue)
            continue
        import copy
        new_cue = copy.deepcopy(cue)
        winner_entry.append(new_cue)
        winner_keys.add(key)
        if hotcue != "-1":
            winner_hotcue_slots.add(hotcue)
        merged += 1

    if merged == 0:
        return True

    # Write back using safe pattern from external_sync.py
    try:
        from djlib.external_sync import _backup_traktor_collection
        _backup_traktor_collection(collection_path)
    except Exception as exc:
        log.warning("Could not back up Traktor collection: %s", exc)

    try:
        import tempfile, os
        serialized = ET.tostring(root, encoding="unicode", xml_declaration=False)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(collection_path.parent),
            prefix=f".{collection_path.name}.cuemerge-",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n')
            tmp.write(serialized)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, collection_path)
        log.info("Traktor: merged %d cue(s) from %s into %s", merged, Path(dup_path).name, Path(winner_path).name)
    except Exception as exc:
        log.warning("Could not save Traktor collection after cue merge: %s", exc)
        return False

    return True


def remove_traktor_track(dup_path: str, collection_path: Path) -> bool:
    """Remove a track entry from Traktor collection.nml by file path.

    Returns True on success/no-op, False on error.
    """
    if not TRAKTOR_UTILS_AVAILABLE:
        return True

    try:
        tree = ET.parse(str(collection_path))
        root = tree.getroot()
    except Exception as exc:
        log.warning("Could not parse Traktor collection for removal: %s", exc)
        return False

    collection_elem = root.find("COLLECTION")
    if collection_elem is None:
        return True

    target = _nfc(dup_path)
    removed = 0
    for entry in list(collection_elem):
        loc = entry.find("LOCATION")
        if loc is None:
            continue
        if _nfc(_traktor_reconstruct_path(loc)) == target:
            collection_elem.remove(entry)
            removed += 1
            break

    if removed == 0:
        log.debug("Duplicate not found in Traktor collection for removal: %s", dup_path)
        return True

    # Update count
    try:
        entries_val = len(list(collection_elem))
        collection_elem.set("ENTRIES", str(entries_val))
    except Exception:
        pass

    try:
        from djlib.external_sync import _backup_traktor_collection
        _backup_traktor_collection(collection_path)
    except Exception as exc:
        log.warning("Could not back up Traktor collection before removal: %s", exc)

    try:
        import tempfile, os
        serialized = ET.tostring(root, encoding="unicode", xml_declaration=False)
        with tempfile.NamedTemporaryFile(
            "w",
            dir=str(collection_path.parent),
            prefix=f".{collection_path.name}.rmdup-",
            delete=False,
            encoding="utf-8",
        ) as tmp:
            tmp.write('<?xml version="1.0" encoding="UTF-8" standalone="no" ?>\n')
            tmp.write(serialized)
            tmp_path = Path(tmp.name)
        os.replace(tmp_path, collection_path)
        log.info("Traktor: removed entry for %s", Path(dup_path).name)
    except Exception as exc:
        log.warning("Could not save Traktor collection after removal: %s", exc)
        return False

    return True


# ── Orchestrator ───────────────────────────────────────────────────────────────

def merge_and_remove_duplicate(
    winner_path: str,
    dup_path: str,
    *,
    traktor_collection_path: Optional[Path] = None,
    delete_file: bool = True,
) -> Dict[str, Any]:
    """Merge cues from dup into winner, remove dup from DJ software, delete file.

    Args:
        winner_path: Pre-move path of the winner (still in Rekordbox/Traktor DB).
        dup_path: Path of the duplicate to consume.
        traktor_collection_path: Path to collection.nml (None = skip Traktor).
        delete_file: Whether to delete the duplicate file from disk.

    Returns:
        Dict with keys: rb_merged, rb_removed, tk_merged, tk_removed,
                        file_deleted, errors (list of str).
    """
    result: Dict[str, Any] = {
        "rb_merged": False,
        "rb_removed": False,
        "tk_merged": False,
        "tk_removed": False,
        "file_deleted": False,
        "errors": [],
    }

    if not Path(dup_path).exists():
        log.info("Duplicate file already gone, skipping merge: %s", dup_path)
        return result

    # ── Rekordbox ──────────────────────────────────────────────────────────────
    if PYREKORDBOX_AVAILABLE:
        try:
            db = Rekordbox6Database()
            rb_merged = merge_rekordbox_cues(db, winner_path, dup_path)
            rb_removed = remove_rekordbox_track(db, dup_path)
            if rb_merged or rb_removed:
                try:
                    db.commit()
                    result["rb_merged"] = rb_merged
                    result["rb_removed"] = rb_removed
                except Exception as exc:
                    msg = f"Rekordbox commit failed: {exc}"
                    log.warning(msg)
                    result["errors"].append(msg)
        except Exception as exc:
            msg = f"Rekordbox cue merge error: {exc}"
            log.warning(msg)
            result["errors"].append(msg)

    # ── Traktor ────────────────────────────────────────────────────────────────
    if traktor_collection_path and traktor_collection_path.exists():
        try:
            result["tk_merged"] = merge_traktor_cues(winner_path, dup_path, traktor_collection_path)
        except Exception as exc:
            msg = f"Traktor cue merge error: {exc}"
            log.warning(msg)
            result["errors"].append(msg)

        try:
            result["tk_removed"] = remove_traktor_track(dup_path, traktor_collection_path)
        except Exception as exc:
            msg = f"Traktor removal error: {exc}"
            log.warning(msg)
            result["errors"].append(msg)

    # ── Delete file ────────────────────────────────────────────────────────────
    if delete_file:
        try:
            Path(dup_path).unlink()
            result["file_deleted"] = True
            log.info("Deleted duplicate file: %s", dup_path)
        except OSError as exc:
            msg = f"Could not delete duplicate file {dup_path}: {exc}"
            log.warning(msg)
            result["errors"].append(msg)

    return result
