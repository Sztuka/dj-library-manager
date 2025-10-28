from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import sys
import shutil
import hashlib

try:
    import acoustid  # type: ignore[import-not-found]  # pyacoustid
except Exception as e:
    raise RuntimeError(
        "Brak modułu 'pyacoustid'. Uruchom task: 'Setup: create venv & install deps'."
    ) from e


def _project_root() -> Path:
    # PyInstaller: pliki trafiają do tymczasowego katalogu _MEIPASS
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parents[1]


def _locate_fpcalc() -> Path:
    # 1) zmienna środowiskowa
    env = os.environ.get("ACOUSTID_FPCALC")
    if env and Path(env).exists():
        return Path(env)

    # 2) w PATH
    found = shutil.which("fpcalc")
    if found:
        return Path(found)

    # 3) vendored w repo/bundlu
    root = _project_root()
    candidates = [
        root / "bin" / "mac" / "fpcalc",
        root / "bin" / "fpcalc",
        Path.cwd() / "bin" / "mac" / "fpcalc",
    ]
    for c in candidates:
        if c.exists():
            return c

    raise RuntimeError(
        "Nie znaleziono 'fpcalc'. Uruchom task 'Install fpcalc (Homebrew)' albo 'Install fpcalc (Download vendor)'."
    )


def ensure_fpcalc_in_env() -> Path:
    p = _locate_fpcalc()
    os.environ["ACOUSTID_FPCALC"] = str(p)
    return p


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _normalize_fingerprint(fp: Any) -> str:
    """Sprowadza fingerprint do stringa (obsługa bytes/list/str)."""
    if fp is None:
        return ""
    if isinstance(fp, str):
        return fp
    if isinstance(fp, bytes):
        try:
            return fp.decode("utf-8", errors="ignore")
        except Exception:
            return fp.hex()
    if isinstance(fp, (list, tuple)):
        return ",".join(str(x) for x in fp)
    return str(fp)


def audio_fingerprint(path: Path) -> str:
    """
    Zwraca fingerprint (string). Wymaga fpcalc — brak = błąd.
    """
    fpcalc_path = ensure_fpcalc_in_env()
    try:
        duration, fp_raw = acoustid.fingerprint_file(str(path))
        fp = _normalize_fingerprint(fp_raw).strip()
        if not fp:
            raise RuntimeError("fpcalc zwrócił pusty fingerprint.")
        return fp
    except Exception as e:
        raise RuntimeError(
            f"Nie udało się wygenerować fingerprintu dla {path} (użyto {fpcalc_path})."
        ) from e
