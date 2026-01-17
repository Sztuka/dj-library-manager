from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Sequence
from io import BytesIO
import requests

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill, Protection, Border, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.drawing.image import Image as XLImage
from openpyxl.worksheet.worksheet import Worksheet

from djlib.filename import build_final_filename


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    width: float | None = None
    hidden: bool = False
    locked: bool = False  # True = read-only (protected)


UNSORTED_COLUMNS: Sequence[ColumnSpec] = [
    # Read-only metadata (locked)
    ColumnSpec("track_id", hidden=True, width=22, locked=True),
    ColumnSpec("file_path", width=115, locked=True),  # Visible for reference
    ColumnSpec("file_hash", hidden=True, width=30, locked=True),
    ColumnSpec("fingerprint", hidden=True, width=26, locked=True),
    ColumnSpec("added_date", hidden=True, width=18, locked=True),
    ColumnSpec("is_duplicate", hidden=True, width=12, locked=True),
    ColumnSpec("tag_artist_original", width=26, locked=True),  # Visible
    ColumnSpec("tag_title_original", width=26, locked=True),   # Visible
    ColumnSpec("tag_genre_original", width=22, locked=True),   # Visible
    ColumnSpec("tag_bpm_original", hidden=True, width=14, locked=True),
    ColumnSpec("tag_key_original", hidden=True, width=14, locked=True),
    ColumnSpec("artist_suggest", hidden=True, width=24, locked=True),
    ColumnSpec("title_suggest", hidden=True, width=24, locked=True),
    ColumnSpec("title_normalized", hidden=False, width=12, locked=True),  # "yes" if title was normalized from MB canonical
    ColumnSpec("version_suggest", hidden=True, width=20, locked=True),
    ColumnSpec("genre_suggest", hidden=False, width=24, locked=True),
    ColumnSpec("album_suggest", hidden=True, width=32, locked=True),  # Hidden - user manages own albums
    ColumnSpec("release_group_id", hidden=True, width=36, locked=True),  # MusicBrainz MBID (kept for reference)
    ColumnSpec("year_suggest", hidden=False, width=12, locked=True),
    ColumnSpec("duration_suggest", hidden=True, width=16, locked=True),
    # Cover art fetching removed - we use standard DJ Library cover embedded during apply
    # See djlib/legacy/coverart_fetch.py for the original implementation
    # Genre hints (visible for decision making, but locked)
    ColumnSpec("genres_musicbrainz", width=24, locked=True),
    ColumnSpec("genres_lastfm", width=24, locked=True),
    ColumnSpec("genres_soundcloud", width=24, locked=True),
    ColumnSpec("genres_beatport", width=24, locked=True),
    ColumnSpec("pop_playcount", width=14, locked=True),
    ColumnSpec("pop_listeners", width=14, locked=True),
    ColumnSpec("meta_source", hidden=True, width=20, locked=True),
    # MusicBrainz canonical first release data (hidden, locked)
    ColumnSpec("original_album_title", hidden=True, width=32, locked=True),
    ColumnSpec("original_release_date", hidden=True, width=18, locked=True),
    ColumnSpec("original_release_year", hidden=True, width=12, locked=True),
    ColumnSpec("original_release_mbid", hidden=True, width=36, locked=True),
    ColumnSpec("original_release_group_mbid", hidden=True, width=36, locked=True),
    ColumnSpec("original_release_category", hidden=True, width=16, locked=True),
    ColumnSpec("original_release_source", hidden=True, width=24, locked=True),
    # Editable fields (user must review/accept)
    ColumnSpec("artist", width=30),
    ColumnSpec("title", width=45),
    ColumnSpec("version_info", width=42),
    ColumnSpec("year", width=10),
    ColumnSpec("genre", width=24),  # User-selected genre (dropdown from genres.yml)
    ColumnSpec("genre_mapping_status", width=18, locked=True),  # "OK" or "UNMAPPED"
    ColumnSpec("status", width=12),  # accept / reject / review
    ColumnSpec("destination", width=14),  # library / reject / archive
    ColumnSpec("must_play", width=14),
    ColumnSpec("occasion_tags", width=24),
    ColumnSpec("notes", width=40),
    ColumnSpec("bpm", width=10),
    ColumnSpec("key_camelot", width=12),
    # Computed final filename (locked)
    ColumnSpec("final_filename", width=100, locked=True),
    # Status column (editable dropdown)
    ColumnSpec("done", width=10),
]

DONE_CHOICES = ("TRUE", "FALSE")
STATUS_CHOICES = ("accept", "reject", "review", "")
DESTINATION_CHOICES = ("library", "reject", "archive", "mixes", "")


def _as_str(val: object | None) -> str:
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    return str(val)


def normalize_unsorted_row(row: Mapping[str, str | None]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for col in UNSORTED_COLUMNS:
        out[col.name] = _as_str(row.get(col.name, ""))
    if not out.get("done"):
        out["done"] = "FALSE"
    
    # Compute final_filename preview
    artist = out.get("artist", "")
    title = out.get("title", "")
    version = out.get("version_info", "")
    bpm = out.get("bpm", "")
    key = out.get("key_camelot", "")
    # Get extension from file_path if available
    file_path = out.get("file_path", "")
    ext = Path(file_path).suffix if file_path else ".mp3"
    
    out["final_filename"] = build_final_filename(artist, title, version, key, bpm, ext)
    
    return out


def load_unsorted_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    wb = load_workbook(path)
    ws: Worksheet = wb.active  # type: ignore[assignment]
    header_row = next(ws.iter_rows(min_row=1, max_row=1))
    headers: List[str] = [_as_str(cell.value) for cell in header_row]
    rows: List[Dict[str, str]] = []
    for excel_row in ws.iter_rows(min_row=2, values_only=True):
        if not excel_row:
            continue
        if all(v in (None, "") for v in excel_row):
            continue
        rec: Dict[str, str] = {}
        for idx, header in enumerate(headers):
            if not header:
                continue
            value = excel_row[idx] if idx < len(excel_row) else ""
            rec[header] = _as_str(value)
        rows.append(normalize_unsorted_row(rec))
    return rows


def write_unsorted_rows(path: Path, rows: Iterable[Dict[str, str]], bucket_choices: Sequence[str]) -> None:
    """Write rows to unsorted.xlsx with new status/destination model.
    
    Args:
        path: Path to unsorted.xlsx
        rows: Row data dictionaries
        bucket_choices: Legacy parameter (ignored, kept for compatibility)
    """
    wb = Workbook()
    ws: Worksheet = wb.active  # type: ignore[assignment]
    ws.title = "Unsorted"

    # Header
    header_font = Font(bold=True, size=16)
    header_fill = PatternFill("solid", fgColor="DDDDDD")
    row_fill_even = PatternFill("solid", fgColor="FFFFFF")
    row_fill_odd = PatternFill("solid", fgColor="E8F0FE")  # Light blue-grey for better contrast
    for col_idx, spec in enumerate(UNSORTED_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=spec.name)
        cell.font = header_font
        cell.fill = header_fill

    # Rows
    normalized_rows = [normalize_unsorted_row(r) for r in rows]
    
    # Styling for editable cells
    editable_font = Font(bold=True, size=16)
    readonly_font = Font(size=16)
    red_font = Font(bold=True, size=16, color='FF0000')  # Red for differing values
    editable_border = Border(
        left=Side(style='medium', color='A8C7FA'),   # Light blue, medium weight
        right=Side(style='medium', color='A8C7FA'),
        top=Side(style='medium', color='A8C7FA'),
        bottom=Side(style='medium', color='A8C7FA')
    )
    
    for row_idx, data in enumerate(normalized_rows, start=2):
        row_fill = row_fill_even if row_idx % 2 == 0 else row_fill_odd
        for col_idx, spec in enumerate(UNSORTED_COLUMNS, start=1):
            # Get cell value
            cell_value = data.get(spec.name, "")
            
            # Special handling for BPM - convert to float for European Excel (comma as decimal separator)
            if spec.name == "bpm" and cell_value:
                try:
                    # Convert string like "112.57" to float 112.57
                    # Excel will automatically format it with comma in European locale
                    cell_value = float(cell_value)
                except (ValueError, TypeError):
                    pass  # Keep as string if conversion fails
            
            # Special handling for final_filename - use Excel formula
            if spec.name == "final_filename":
                # Find column indices for formula
                artist_col = next(i for i, s in enumerate(UNSORTED_COLUMNS, start=1) if s.name == "artist")
                title_col = next(i for i, s in enumerate(UNSORTED_COLUMNS, start=1) if s.name == "title")
                version_col = next(i for i, s in enumerate(UNSORTED_COLUMNS, start=1) if s.name == "version_info")
                key_col = next(i for i, s in enumerate(UNSORTED_COLUMNS, start=1) if s.name == "key_camelot")
                bpm_col = next(i for i, s in enumerate(UNSORTED_COLUMNS, start=1) if s.name == "bpm")
                
                # Convert to Excel column letters
                artist_letter = get_column_letter(artist_col)
                title_letter = get_column_letter(title_col)
                version_letter = get_column_letter(version_col)
                key_letter = get_column_letter(key_col)
                bpm_letter = get_column_letter(bpm_col)
                
                # Get file extension from original path
                file_path_val = data.get("file_path", "")
                ext = file_path_val.split(".")[-1] if "." in file_path_val else "mp3"
                
                # Build Excel formula for filename
                # Format: Artist - Title (Version) [Key BPM].ext
                # Version part is added only if not empty
                formula = (
                    f'={artist_letter}{row_idx}&" - "&{title_letter}{row_idx}'
                    f'&IF({version_letter}{row_idx}<>""," ("&{version_letter}{row_idx}&")","")'
                    f'&" ["&{key_letter}{row_idx}&" "&ROUND({bpm_letter}{row_idx},0)&"].{ext}"'
                )
                cell = ws.cell(row=row_idx, column=col_idx, value=formula)
            else:
                cell = ws.cell(row=row_idx, column=col_idx, value=cell_value)
            
            cell.fill = row_fill
            
            # Check if BPM/Key differs from original tags
            use_red = False
            if spec.name == "bpm":
                orig_bpm = (data.get("tag_bpm_original", "") or "").strip()
                curr_bpm = (data.get("bpm", "") or "").strip()
                # Compare rounded BPM values to avoid false positives from precision differences
                if orig_bpm and curr_bpm:
                    try:
                        orig_rounded = round(float(orig_bpm))
                        curr_rounded = round(float(curr_bpm))
                        if orig_rounded != curr_rounded:
                            use_red = True
                    except (ValueError, TypeError):
                        if orig_bpm != curr_bpm:
                            use_red = True
            elif spec.name == "key_camelot":
                orig_key = (data.get("tag_key_original", "") or "").strip()
                curr_key = (data.get("key_camelot", "") or "").strip()
                if orig_key and curr_key and orig_key != curr_key:
                    use_red = True
            elif spec.name == "genre_mapping_status":
                # Highlight UNMAPPED in orange
                if cell_value == "UNMAPPED":
                    cell.fill = PatternFill(start_color="FFA500", end_color="FFA500", fill_type="solid")
                    cell.font = Font(bold=True, color="000000")
            
            # Apply protection to locked columns
            if spec.locked:
                cell.protection = Protection(locked=True)
                cell.font = readonly_font
            else:
                cell.protection = Protection(locked=False)
                cell.font = red_font if use_red else editable_font
                cell.border = editable_border
        ws.row_dimensions[row_idx].height = 30  # Standard row height

    # Cover art image insertion removed - we use standard DJ Library cover
    # See djlib/legacy/coverart_fetch.py for the original implementation

    # Column formatting
    for idx, spec in enumerate(UNSORTED_COLUMNS, start=1):
        letter = get_column_letter(idx)
        col_dim = ws.column_dimensions[letter]
        if spec.width is not None:
            col_dim.width = spec.width
        col_dim.hidden = spec.hidden
        
        # Set number format for BPM column (2 decimal places)
        if spec.name == "bpm":
            for row in range(2, len(normalized_rows) + 2):
                cell = ws.cell(row=row, column=idx)
                cell.number_format = '0.00'

    ws.freeze_panes = "A2"
    last_col_letter = get_column_letter(len(UNSORTED_COLUMNS))
    last_row = max(1, len(normalized_rows) + 1)
    ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"

    # Validation lists sheet
    lists_ws = wb.create_sheet("_lists")
    
    # Genre labels (from genres.yml)
    from djlib.genre_canonical import get_genre_labels
    try:
        genre_labels = get_genre_labels()
        for idx, label in enumerate(genre_labels, start=1):
            lists_ws.cell(row=idx, column=1, value=label)
    except Exception:
        genre_labels = []
    
    # Status choices
    for idx, status in enumerate(STATUS_CHOICES, start=1):
        lists_ws.cell(row=idx, column=2, value=status)
    
    # Destination choices
    for idx, dest in enumerate(DESTINATION_CHOICES, start=1):
        lists_ws.cell(row=idx, column=3, value=dest)
    
    # Done choices
    lists_ws.cell(row=1, column=4, value=DONE_CHOICES[0])
    lists_ws.cell(row=2, column=4, value=DONE_CHOICES[1])
    
    # Cover art action choices removed - we use standard DJ Library cover
    
    # Legacy bucket choices (if provided, for backward compatibility)
    if bucket_choices:
        for idx, bucket in enumerate(bucket_choices, start=1):
            lists_ws.cell(row=idx, column=6, value=_as_str(bucket))
    
    lists_ws.sheet_state = "hidden"

    # Data validation for genre
    try:
        genre_col_idx = [i for i, spec in enumerate(UNSORTED_COLUMNS, start=1) if spec.name == "genre"][0]
        genre_letter = get_column_letter(genre_col_idx)
        if genre_labels:
            formula = f"'_lists'!$A$1:$A${len(genre_labels)}"
            dv_genre = DataValidation(type="list", formula1=formula, allow_blank=True, showDropDown=False)
            dv_genre.error = "Select genre from the list"
            dv_genre.errorTitle = "Invalid genre"
            ws.add_data_validation(dv_genre)
            dv_genre.add(f"{genre_letter}2:{genre_letter}1048576")
    except Exception:
        pass

    # Data validation for status column
    try:
        status_idx = [i for i, spec in enumerate(UNSORTED_COLUMNS, start=1) if spec.name == "status"][0]
        status_letter = get_column_letter(status_idx)
        dv_status = DataValidation(type="list", formula1=f"'_lists'!$B$1:$B${len(STATUS_CHOICES)}", 
                                   allow_blank=True, showDropDown=False)
        dv_status.error = "Use accept/reject/review"
        dv_status.errorTitle = "Invalid status"
        ws.add_data_validation(dv_status)
        dv_status.add(f"{status_letter}2:{status_letter}1048576")
    except Exception:
        pass

    # Data validation for destination column
    try:
        dest_idx = [i for i, spec in enumerate(UNSORTED_COLUMNS, start=1) if spec.name == "destination"][0]
        dest_letter = get_column_letter(dest_idx)
        dv_dest = DataValidation(type="list", formula1=f"'_lists'!$C$1:$C${len(DESTINATION_CHOICES)}", 
                                allow_blank=True, showDropDown=False)
        dv_dest.error = "Use library/reject/archive/mixes"
        dv_dest.errorTitle = "Invalid destination"
        ws.add_data_validation(dv_dest)
        dv_dest.add(f"{dest_letter}2:{dest_letter}1048576")
    except Exception:
        pass

    # Data validation for done column
    try:
        done_idx = [i for i, spec in enumerate(UNSORTED_COLUMNS, start=1) if spec.name == "done"][0]
        done_letter = get_column_letter(done_idx)
        dv_done = DataValidation(type="list", formula1="'_lists'!$D$1:$D$2", allow_blank=False, showDropDown=False)
        dv_done.error = "Use TRUE/FALSE"
        dv_done.errorTitle = "Invalid value"
        ws.add_data_validation(dv_done)
        dv_done.add(f"{done_letter}2:{done_letter}1048576")
    except Exception:
        pass
    
    # Legacy: target_subfolder validation removed (column no longer exists)
    # Bucket-based taxonomy (CLUB/OPEN FORMAT) has been replaced with simple logistics (library/reject/archive)

    # Enable sheet protection (locked cells will be protected, unlocked cells editable)
    ws.protection.sheet = True
    # No password needed - just enable protection to respect locked/unlocked cells

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def is_done(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().upper() in {"TRUE", "YES", "1", "DONE"}
