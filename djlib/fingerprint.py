from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import sys
import shutil
import hashlib
import platform
import subprocess

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


def _ensure_executable(p: Path) -> Path:
    try:
        mode = p.stat().st_mode
        # jeśli brak bitu exec dla ownera – ustaw 0o755
        if not os.access(p, os.X_OK):
            p.chmod(0o755)
        # usuń kwarantannę na macOS (gdyby plik był pobrany z sieci)
        if platform.system().lower() == "darwin":
            try:
                subprocess.run(["xattr", "-d", "com.apple.quarantine", str(p)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass
    except Exception:
        pass
    return p


def _locate_fpcalc() -> Path:
    # 1) Vendored w repo/bundlu (preferowane – działa offline)
    root = _project_root()
    candidates = [
        root / "bin" / "mac" / "fpcalc",
        root / "bin" / "fpcalc",
        Path.cwd() / "bin" / "mac" / "fpcalc",
    ]
    for c in candidates:
        if c.exists():
            return _ensure_executable(c)

    # 2) zmienna środowiskowa
    env = os.environ.get("ACOUSTID_FPCALC")
    if env and Path(env).exists():
        return _ensure_executable(Path(env))

    # 3) w PATH
    found = shutil.which("fpcalc")
    if found:
        return _ensure_executable(Path(found))

    # 4) Opcjonalna próba online (wyłączona domyślnie). Ustaw DJLIB_ALLOW_ONLINE_FPCALC=1, aby użyć instalatora.
    allow_online = os.environ.get("DJLIB_ALLOW_ONLINE_FPCALC", "0") == "1"
    if allow_online and platform.system().lower() == "darwin":
        try:
            installer = _project_root() / "scripts" / "install_fpcalc.py"
            if installer.exists():
                subprocess.run([sys.executable, str(installer)], check=False)
                found = shutil.which("fpcalc")
                if found:
                    return _ensure_executable(Path(found))
                for c in candidates:
                    if c.exists():
                        return _ensure_executable(c)
        except Exception:
            pass
    # 5) Brak – podaj instrukcję offline
    raise RuntimeError(
        "Nie znaleziono 'fpcalc'. Tryb offline: umieść binarkę w 'bin/mac/fpcalc' (lub ustaw ACOUSTID_FPCALC na ścieżkę binarki) i uruchom ponownie."
    )


def ensure_fpcalc_in_env() -> Path:
    p = _locate_fpcalc()
    # Ustaw obie zmienne środowiskowe:
    # - FPCALC: używana przez pyacoustid (patrz FPCALC_ENVVAR = 'FPCALC')
    # - ACOUSTID_FPCALC: nasze wewnętrzne/kompatybilność wsteczna
    os.environ["FPCALC"] = str(p)
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


def fingerprint_info(path: Path) -> tuple[int, str]:
    """Zwraca (duration_sec, fingerprint_str). Wymaga fpcalc.
    duration zwracamy jako int sekund.
    """
    fpcalc_path = ensure_fpcalc_in_env()
    # 1) Spróbuj przez pyacoustid (najprościej)
    try:
        duration_val, fp_raw = acoustid.fingerprint_file(str(path))
        fp = _normalize_fingerprint(fp_raw).strip()
        duration_sec = 0
        try:
            # pyacoustid zwraca sekundy (int), ale bywa float
            duration_sec = int(round(float(duration_val)))
        except Exception:
            duration_sec = 0
        if fp:
            return max(0, duration_sec), fp
    except Exception:
        # spróbujemy fallbackiem
        pass

    # 2) Fallback: wywołaj fpcalc bezpośrednio (bez pyacoustid)
    try:
        out = subprocess.run([str(fpcalc_path), "-json", str(path)], capture_output=True, text=True, check=False)
        txt = out.stdout.strip()
        # jeśli -json nie wspierane, spróbuj zwykłego trybu
        if out.returncode != 0 or not txt:
            out = subprocess.run([str(fpcalc_path), str(path)], capture_output=True, text=True, check=False)
            txt = out.stdout.strip()
        duration_sec = 0
        fp = ""
        if txt.startswith("{"):
            # JSON
            try:
                import json as _json
                j = _json.loads(txt)
                duration_sec = int(round(float(j.get("duration", 0))))
                # fingerprint może być listą liczb lub stringiem
                fp = _normalize_fingerprint(j.get("fingerprint", "")).strip()
            except Exception:
                pass
        else:
            # Parsuj linie DURATION=…, FINGERPRINT=…
            for line in txt.splitlines():
                if line.startswith("DURATION="):
                    try:
                        duration_sec = int(round(float(line.split("=",1)[1])))
                    except Exception:
                        pass
                elif line.startswith("FINGERPRINT="):
                    fp = line.split("=",1)[1].strip()
        if fp:
            return max(0, duration_sec), fp
    except Exception:
        pass

    # 3) Niepowodzenie
    raise RuntimeError(
        f"Nie udało się wygenerować fingerprintu dla {path} (próbowano: pyacoustid i {fpcalc_path})."
    )


def audio_fingerprint(path: Path) -> str:
    """Zachowana kompatybilność: zwraca tylko fingerprint."""
    _, fp = fingerprint_info(path)
    return fp
