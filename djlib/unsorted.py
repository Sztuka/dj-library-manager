from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Mapping, Sequence

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.worksheet import Worksheet


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    width: float | None = None
    hidden: bool = False


UNSORTED_COLUMNS: Sequence[ColumnSpec] = [
    ColumnSpec("track_id", hidden=True, width=22),
    ColumnSpec("file_path", hidden=True, width=36),
    ColumnSpec("file_hash", hidden=True, width=30),
    ColumnSpec("fingerprint", hidden=True, width=26),
    ColumnSpec("added_date", hidden=True, width=18),
    ColumnSpec("is_duplicate", hidden=True, width=12),
    ColumnSpec("tag_artist_original", hidden=True, width=26),
    ColumnSpec("tag_title_original", hidden=True, width=26),
    ColumnSpec("tag_genre_original", hidden=True, width=22),
    ColumnSpec("tag_bpm_original", hidden=True, width=14),
    ColumnSpec("tag_key_original", hidden=True, width=14),
    ColumnSpec("artist_suggest", hidden=True, width=24),
    ColumnSpec("title_suggest", hidden=True, width=24),
    ColumnSpec("version_suggest", hidden=True, width=20),
    ColumnSpec("genre_suggest", hidden=True, width=24),
    ColumnSpec("album_suggest", hidden=True, width=22),
    ColumnSpec("year_suggest", hidden=True, width=12),
    ColumnSpec("duration_suggest", hidden=True, width=16),
    ColumnSpec("genres_musicbrainz", hidden=True, width=24),
    ColumnSpec("genres_lastfm", hidden=True, width=24),
    ColumnSpec("genres_soundcloud", hidden=True, width=24),
    ColumnSpec("pop_playcount", width=14),
    ColumnSpec("pop_listeners", width=14),
    ColumnSpec("meta_source", hidden=True, width=20),
    ColumnSpec("ai_guess_bucket", hidden=True, width=28),
    ColumnSpec("ai_guess_comment", hidden=True, width=30),
    ColumnSpec("artist", width=26),
    ColumnSpec("title", width=32),
    ColumnSpec("version_info", width=24),
    ColumnSpec("genre", width=20),
    ColumnSpec("target_subfolder", width=34),
    ColumnSpec("must_play", width=14),
    ColumnSpec("occasion_tags", width=24),
    ColumnSpec("notes", width=36),
    ColumnSpec("bpm", width=10),
    ColumnSpec("key_camelot", width=12),
    ColumnSpec("energy_hint", width=14),
    ColumnSpec("done", width=10),
]

DONE_CHOICES = ("TRUE", "FALSE")


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
    wb = Workbook()
    ws: Worksheet = wb.active  # type: ignore[assignment]
    ws.title = "Unsorted"

    # Header
    header_font = Font(bold=True)
    header_fill = PatternFill("solid", fgColor="DDDDDD")
    for col_idx, spec in enumerate(UNSORTED_COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=spec.name)
        cell.font = header_font
        cell.fill = header_fill

    # Rows
    normalized_rows = [normalize_unsorted_row(r) for r in rows]
    for row_idx, data in enumerate(normalized_rows, start=2):
        for col_idx, spec in enumerate(UNSORTED_COLUMNS, start=1):
            ws.cell(row=row_idx, column=col_idx, value=data.get(spec.name, ""))
        ws.row_dimensions[row_idx].height = 20

    # Column formatting
    for idx, spec in enumerate(UNSORTED_COLUMNS, start=1):
        letter = get_column_letter(idx)
        col_dim = ws.column_dimensions[letter]
        if spec.width is not None:
            col_dim.width = spec.width
        col_dim.hidden = spec.hidden

    ws.freeze_panes = "A2"
    last_col_letter = get_column_letter(len(UNSORTED_COLUMNS))
    last_row = max(1, len(normalized_rows) + 1)
    ws.auto_filter.ref = f"A1:{last_col_letter}{last_row}"

    # Validation lists sheet
    lists_ws = wb.create_sheet("_lists")
    for idx, bucket in enumerate(bucket_choices, start=1):
        lists_ws.cell(row=idx, column=1, value=_as_str(bucket))
    lists_ws.cell(row=1, column=2, value=DONE_CHOICES[0])
    lists_ws.cell(row=2, column=2, value=DONE_CHOICES[1])
    lists_ws.sheet_state = "hidden"

    # Data validation for target_subfolder
    try:
        target_col_idx = [i for i, spec in enumerate(UNSORTED_COLUMNS, start=1) if spec.name == "target_subfolder"][0]
        target_letter = get_column_letter(target_col_idx)
        if bucket_choices:
            formula = f"'_lists'!$A$1:$A${len(bucket_choices)}"
        else:
            formula = '"READY TO PLAY/UNSORTED"'
        dv_target = DataValidation(type="list", formula1=formula, allow_blank=True, showDropDown=True)
        dv_target.error = "Select bucket from the list"
        dv_target.errorTitle = "Invalid bucket"
        dv_target.ranges.append(f"{target_letter}2:{target_letter}{last_row}")
        ws.add_data_validation(dv_target)
    except Exception:
        pass

    # Data validation for done column
    try:
        done_idx = [i for i, spec in enumerate(UNSORTED_COLUMNS, start=1) if spec.name == "done"][0]
        done_letter = get_column_letter(done_idx)
        dv_done = DataValidation(type="list", formula1="'_lists'!$B$1:$B$2", allow_blank=False)
        dv_done.error = "Use TRUE/FALSE"
        dv_done.errorTitle = "Invalid value"
        dv_done.ranges.append(f"{done_letter}2:{done_letter}{last_row}")
        ws.add_data_validation(dv_done)
    except Exception:
        pass

    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def is_done(value: str | None) -> bool:
    if value is None:
        return False
    return str(value).strip().upper() in {"TRUE", "YES", "1", "DONE"}
