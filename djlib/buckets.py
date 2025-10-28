from __future__ import annotations
from typing import Iterable, List
from djlib.taxonomy import allowed_targets as _allowed, is_valid_target as _is_valid

def is_valid_target(value: str) -> bool:
    return _is_valid(value)

def list_allowed() -> Iterable[str]:
    return _allowed()
