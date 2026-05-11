"""Last-Write-Wins (LWW) per-field merge for Phase 3 gig-merge.

Computes the resolved library.csv row for a track that was on a gig,
given three snapshots:

  baseline  — gig.csv row captured at gig-prep COMMIT (ground truth: state
              before the gig started)
  live      — current library.csv row (may have been updated by sync-dj-libraries
              or other operations while the gig was running)
  fresh     — fields read from Rekordbox master6.db after the gig (what the
              DJ actually changed: cue points, ratings, play count)

Algorithm per field:
  if fresh != baseline:            → DJ changed it during the gig → use fresh
  elif live != baseline:           → changed elsewhere (other sync) → keep live
  else:                            → no change anywhere → no-op

Conflict (fresh≠baseline AND live≠baseline AND fresh≠live):
  fresh wins (live gig edits outrank automated syncs), flagged in audit log.

Only fields listed in MERGE_FIELDS are evaluated; all other fields are taken
from `live` unchanged.

Known limitations:
  play_count — LWW (fresh wins on conflict) rather than MAX. If sync
    incremented the count while the DJ also played the track, the lower
    of the two counts wins when fresh < live. In practice this means at
    most a few plays are under-counted; the alternative (MAX) would
    overcounts after a rollback. LWW is the simpler invariant.

  cue_points_rb — compared as raw JSON strings. serialize_rb_cues always
    uses separators=(",",":") so format is deterministic as long as all
    writers use the same serializer. A whitespace difference would be a
    false "fresh changed" — treat as harmless overwrite.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Fields that may be updated in Rekordbox during a gig and should be
# considered for LWW merge.  Must be a subset of LIBRARY_FIELDNAMES.
MERGE_FIELDS: List[str] = [
    "cue_points_rb",
    "rating",
    "play_count",
    "bpm",
]


@dataclass
class AuditEntry:
    track_id: str
    field_name: str
    baseline_value: str
    live_value: str
    fresh_value: str
    resolved_value: str
    source: str          # "fresh" | "live" | "no_change"
    conflict: bool = False


def merge_track(
    track_id: str,
    baseline: Dict[str, str],
    live: Dict[str, str],
    fresh: Dict[str, str],
    merge_fields: Optional[List[str]] = None,
) -> Tuple[Dict[str, str], List[AuditEntry]]:
    """Compute the LWW-resolved row and audit log for one track.

    Args:
        track_id:     used in AuditEntry only, not for lookup
        baseline:     gig.csv row (state before gig)
        live:         current library.csv row
        fresh:        rekordbox_reader output for this track (may be partial —
                      only fields present in fresh are evaluated)
        merge_fields: override default MERGE_FIELDS (useful for testing)

    Returns:
        (resolved_row, audit_entries)
        resolved_row is a copy of `live` with LWW-applied fields.
        audit_entries lists every field where at least one value differed.
    """
    # Use a copy so callers cannot mutate MERGE_FIELDS via the default
    fields = list(merge_fields) if merge_fields is not None else list(MERGE_FIELDS)
    resolved = dict(live)
    audit: List[AuditEntry] = []

    for fname in fields:
        b  = str(baseline.get(fname, "") or "")
        lv = str(live.get(fname, "") or "")
        f  = str(fresh.get(fname, "") or "")

        # If fresh has no data for this field (not returned by rekordbox_reader),
        # treat as "no change from DJ side" — keep live.
        if fname not in fresh:
            continue

        fresh_changed = f != b
        live_changed  = lv != b
        conflict      = fresh_changed and live_changed and f != lv

        if fresh_changed:
            resolved[fname] = f
            source = "fresh"
        elif live_changed:
            # resolved already has live value (copied above)
            source = "live"
        else:
            source = "no_change"

        if fresh_changed or live_changed:
            audit.append(AuditEntry(
                track_id=track_id,
                field_name=fname,
                baseline_value=b,
                live_value=lv,
                fresh_value=f,
                resolved_value=resolved[fname],
                source=source,
                conflict=conflict,
            ))

    return resolved, audit


def merge_gig_tracks(
    gig_rows: List[Dict[str, str]],
    live_rows_by_tid: Dict[str, Dict[str, str]],
    fresh_by_tid: Dict[str, Dict[str, str]],
    merge_fields: Optional[List[str]] = None,
) -> Tuple[List[Dict[str, str]], List[AuditEntry]]:
    """Batch LWW merge for all tracks in a gig.

    Args:
        gig_rows:        rows from gig.csv (baseline snapshot)
        live_rows_by_tid: {track_id: row} from current library.csv
        fresh_by_tid:    {track_id: fields} from rekordbox_reader
                         (tracks absent here get live values unchanged)

    Returns:
        (resolved_rows, all_audit_entries)
        resolved_rows preserves order of gig_rows.
    """
    all_resolved: List[Dict[str, str]] = []
    all_audit: List[AuditEntry] = []

    for baseline in gig_rows:
        tid = str(baseline.get("track_id", "") or "")
        if not tid:
            log.warning("merge_gig_tracks: gig_row with empty track_id skipped")
            continue

        if tid not in live_rows_by_tid:
            # Track vanished from library.csv between prep and merge.
            # Fall back to baseline so merge_track still runs (fresh fields
            # will be applied), but non-MERGE_FIELDS come from the old snapshot.
            # This is data loss for fields edited outside the gig — logged so
            # the operator can investigate.
            log.warning(
                "track_id=%s not found in library.csv — using gig.csv snapshot "
                "as live fallback; edits to non-merge fields since gig-prep are lost",
                tid,
            )
        live = live_rows_by_tid.get(tid, baseline)
        fresh = fresh_by_tid.get(tid, {})

        resolved, audit = merge_track(
            track_id=tid,
            baseline=baseline,
            live=live,
            fresh=fresh,
            merge_fields=merge_fields,
        )
        all_resolved.append(resolved)
        all_audit.extend(audit)

    return all_resolved, all_audit
