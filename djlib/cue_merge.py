"""Cue point merging for DJ software databases.

When a track has acoustic duplicates (same song, different path/quality), this
module merges the duplicate's cue points into the winner and removes the
duplicate entry from Traktor. Rekordbox deletion is intentionally NOT attempted
(pyrekordbox doesn't support reliable track removal — user handles it manually).

Called from cmd_apply when a row has a non-empty duplicate_paths field.
"""
from __future__ import annotations

import copy
import json
import logging
import unicodedata
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
    from traktor_nml_utils.models.collection import CueV2Type
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
    path_nfc = _nfc(file_path)
    for content in db.get_content():
        fp = _nfc(getattr(content, "FolderPath", "") or "")
        if fp == path_nfc:
            return content
    return None


def merge_rekordbox_cues(db: Any, winner_path: str, dup_path: str) -> bool:
    """Copy cues from dup_path's Rekordbox entry into winner_path's entry.

    Safe: only adds cue rows, never deletes. Rekordbox must be closed (caller
    is responsible for checking get_rekordbox_pid() before calling).
    Caller commits once after all duplicates are processed.

    Returns True on success/no-op, False on unexpected error.
    """
    dup_content = _rb_find_content(db, dup_path)
    if dup_content is None:
        log.debug("Duplicate not in Rekordbox, nothing to merge: %s", dup_path)
        return True

    winner_content = _rb_find_content(db, winner_path)
    if winner_content is None:
        log.warning("Winner not found in Rekordbox DB: %s", winner_path)
        return False

    try:
        dup_cues = list(dup_content.Cues or [])
        winner_cues = list(winner_content.Cues or [])
    except Exception as exc:
        log.warning("Could not read Rekordbox cues: %s", exc)
        return False

    winner_positions = {(c.InMsec, c.Kind) for c in winner_cues}
    winner_hotcue_slots = {c.Kind for c in winner_cues if c.Kind and c.Kind > 0}

    merged = 0
    for cue in dup_cues:
        key = (cue.InMsec, cue.Kind)
        if key in winner_positions:
            continue
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
            log.warning("Failed to add cue (InMsec=%s Kind=%s): %s", cue.InMsec, cue.Kind, exc)

    log.info("Rekordbox: merged %d cue(s) from %s into %s", merged, Path(dup_path).name, Path(winner_path).name)
    return True


# ── Traktor ────────────────────────────────────────────────────────────────────

def _traktor_path_from_location(loc: Any) -> str:
    """Reconstruct full file path from a Traktor Locationtype model object."""
    dir_path: str = getattr(loc, "dir", "") or ""
    file_name: str = getattr(loc, "file", "") or ""
    volume: str = getattr(loc, "volume", "") or ""
    parts = [p for p in dir_path.split("/:") if p]
    if volume and volume != "Macintosh HD":
        return str(Path("/Volumes") / volume / "/".join(parts) / file_name)
    return str(Path("/") / "/".join(parts) / file_name)


def _traktor_find_entry(collection: Any, file_path: str) -> Optional[Any]:
    """Find a Traktor entry by file path using the traktor_nml_utils model."""
    target = _nfc(file_path)
    for entry in collection.nml.collection.entry:
        if not entry.location:
            continue
        reconstructed = _nfc(_traktor_path_from_location(entry.location))
        if reconstructed == target:
            return entry
    return None


def merge_traktor_cues(winner_path: str, dup_path: str, collection_path: Path) -> bool:
    """Merge Traktor cue_v2 entries from duplicate into winner.

    Uses traktor_nml_utils model + XmlSerializer (same path as existing code)
    so the saved NML is always well-formed.

    Returns True on success/no-op, False on error.
    """
    if not TRAKTOR_UTILS_AVAILABLE:
        log.debug("traktor-nml-utils not available, skipping Traktor cue merge")
        return True

    try:
        collection = TraktorCollection(collection_path)
    except Exception as exc:
        log.warning("Could not load Traktor collection: %s", exc)
        return False

    dup_entry = _traktor_find_entry(collection, dup_path)
    if dup_entry is None:
        log.debug("Duplicate not in Traktor collection: %s", dup_path)
        return True

    winner_entry = _traktor_find_entry(collection, winner_path)
    if winner_entry is None:
        log.warning("Winner not found in Traktor collection: %s", winner_path)
        return False

    dup_cues = dup_entry.cue_v2 or []
    if not dup_cues:
        return True

    winner_cues = winner_entry.cue_v2 or []
    # Identify occupied slots — (start, hotcue) pair is the uniqueness key
    winner_keys = {(c.start, c.hotcue) for c in winner_cues}
    winner_hotcue_slots = {c.hotcue for c in winner_cues if c.hotcue is not None and c.hotcue >= 0}

    merged = 0
    for cue in dup_cues:
        key = (cue.start, cue.hotcue)
        if key in winner_keys:
            continue
        hotcue_val = cue.hotcue if cue.hotcue is not None else -1
        if hotcue_val >= 0 and hotcue_val in winner_hotcue_slots:
            log.debug("Traktor hotcue slot %d already occupied, skipping", hotcue_val)
            continue
        new_cue = CueV2Type(
            name=cue.name,
            displ_order=cue.displ_order,
            type=cue.type,
            start=cue.start,
            len=cue.len,
            repeats=cue.repeats,
            hotcue=cue.hotcue,
            color=cue.color,
        )
        winner_cues.append(new_cue)
        winner_keys.add(key)
        if hotcue_val >= 0:
            winner_hotcue_slots.add(hotcue_val)
        merged += 1

    if merged == 0:
        return True

    winner_entry.cue_v2 = winner_cues

    try:
        from djlib.external_sync import _backup_traktor_collection
        _backup_traktor_collection(collection_path)
    except Exception as exc:
        log.warning("Could not back up Traktor collection: %s", exc)

    try:
        collection.save()
        log.info("Traktor: merged %d cue(s) from %s into %s", merged, Path(dup_path).name, Path(winner_path).name)
    except Exception as exc:
        log.warning("Could not save Traktor collection after cue merge: %s", exc)
        return False

    return True


def remove_traktor_track(dup_path: str, collection_path: Path) -> bool:
    """Remove a track entry from Traktor collection.nml by file path.

    Uses traktor_nml_utils model + collection.save() for well-formed output.
    Returns True on success/no-op, False on error.
    """
    if not TRAKTOR_UTILS_AVAILABLE:
        return True

    try:
        collection = TraktorCollection(collection_path)
    except Exception as exc:
        log.warning("Could not load Traktor collection for removal: %s", exc)
        return False

    collection_root = collection.nml.collection
    if not collection_root:
        return True

    target = _nfc(dup_path)
    removed = 0
    for i in range(len(collection_root.entry) - 1, -1, -1):
        entry = collection_root.entry[i]
        if not entry.location:
            continue
        if _nfc(_traktor_path_from_location(entry.location)) == target:
            collection_root.entry.pop(i)
            removed += 1
            break

    if removed == 0:
        log.debug("Duplicate not found in Traktor for removal: %s", dup_path)
        return True

    try:
        collection_root.entries = len(collection_root.entry)
    except Exception:
        pass

    try:
        from djlib.external_sync import _backup_traktor_collection
        _backup_traktor_collection(collection_path)
    except Exception as exc:
        log.warning("Could not back up Traktor collection before removal: %s", exc)

    try:
        collection.save()
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
    """Merge cues from dup into winner, remove dup from Traktor, delete file.

    Rekordbox cues are merged (write-only, safe). Rekordbox track DELETION is
    intentionally skipped — pyrekordbox doesn't support reliable removal.
    The user should delete the orphaned Rekordbox entry manually.

    Returns dict: rb_merged, rb_removed (always False), tk_merged, tk_removed,
                  file_deleted, errors (list[str]).
    """
    result: Dict[str, Any] = {
        "rb_merged": False,
        "rb_removed": False,   # always False — Rekordbox deletion not supported
        "tk_merged": False,
        "tk_removed": False,
        "file_deleted": False,
        "errors": [],
    }

    if not Path(dup_path).exists():
        log.info("Duplicate file already gone, skipping: %s", dup_path)
        return result

    # ── Rekordbox: merge cues only (no deletion) ───────────────────────────────
    if PYREKORDBOX_AVAILABLE:
        try:
            db = Rekordbox6Database()
            rb_merged = merge_rekordbox_cues(db, winner_path, dup_path)
            if rb_merged:
                try:
                    db.commit()
                    result["rb_merged"] = True
                except Exception as exc:
                    msg = f"Rekordbox commit failed: {exc}"
                    log.warning(msg)
                    result["errors"].append(msg)
        except Exception as exc:
            msg = f"Rekordbox cue merge error: {exc}"
            log.warning(msg)
            result["errors"].append(msg)

    # ── Traktor: merge cues + remove entry ────────────────────────────────────
    if traktor_collection_path and traktor_collection_path.exists():
        try:
            result["tk_merged"] = merge_traktor_cues(winner_path, dup_path, traktor_collection_path)
        except Exception as exc:
            msg = f"Traktor cue merge error: {exc}"
            log.warning(msg)
            result["errors"].append(msg)

        try:
            # Load fresh after cue merge write (file was saved above)
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
            msg = f"Could not delete {Path(dup_path).name}: {exc}"
            log.warning(msg)
            result["errors"].append(msg)

    return result
