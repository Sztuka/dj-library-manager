#!/usr/bin/env python3
from __future__ import annotations
import sys
import subprocess
from pathlib import Path
import yaml

REPO = Path(__file__).resolve().parents[1]
CFG = REPO / "config.local.yml"

def _expand(p: str | Path) -> Path:
    return Path(str(p)).expanduser().resolve()

def _esc(s: str) -> str:
    # escape dla AppleScript stringa
    return s.replace("\\", "\\\\").replace('"', '\\"')

def _run_osascript(lines: list[str]) -> str:
    cmd = ["osascript"]
    for line in lines:
        cmd += ["-e", line]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or "User cancelled.")
    return (r.stdout or "").strip()

def choose_folder_mac(prompt: str, default: Path | None = None) -> Path:
    """
    Pokazuje natywny 'choose folder'. Jeśli 'default' nie istnieje, użyj jego rodzica,
    a jeśli i ten nie istnieje – nie ustawiaj default location.
    """
    dl_clause = ""
    if default:
        d = _expand(default)
        base = d if d.exists() else d.parent if d.parent.exists() else None
        if base:
            dl_clause = f'default location (POSIX file "{_esc(str(base))}")'
    script = f'choose folder with prompt "{_esc(prompt)}" {dl_clause}'
    # Zwróć jako POSIX path
    out = _run_osascript([f'POSIX path of ({script})'])
    return _expand(out)

def load_defaults() -> tuple[Path, Path]:
    lib = _expand("~/Music_DJ")
    inbox = lib / "INBOX_UNSORTED"
    if CFG.exists():
        with CFG.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
            lib = _expand(data.get("library_root", lib))
            inbox = _expand(data.get("inbox_dir", inbox))
    return lib, inbox

def save_config(library_root: Path, inbox_dir: Path) -> None:
    data = {
        "library_root": str(_expand(library_root)),
        "inbox_dir": str(_expand(inbox_dir)),
    }
    with CFG.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True, sort_keys=False)

    # utwórz strukturę od razu
    libp = _expand(library_root)
    for p in [libp, _expand(inbox_dir), libp / "READY_TO_PLAY", libp / "REVIEW_QUEUE", libp / "LOGS"]:
        p.mkdir(parents=True, exist_ok=True)

def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Ten picker działa na macOS. Na innych systemach użyj tasku 'Configure (choose folders)'.")
    lib_default, inbox_default = load_defaults()
    try:
        lib = choose_folder_mac("Wybierz katalog biblioteki (utworzymy w nim strukturę)", lib_default)
        inbox = choose_folder_mac("Wybierz folder INBOX (nieposortowane pliki do skanowania)", inbox_default)
        save_config(lib, inbox)
        print(f"✅ Zapisano do {CFG}")
        print(f"   library_root = {lib}")
        print(f"   inbox_dir    = {inbox}")
        print("Teraz odpal: Scan → Auto-decide → Apply.")
    except Exception as e:
        print(f"❌ Przerwano: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
