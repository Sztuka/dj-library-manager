#!/usr/bin/env python3
from __future__ import annotations
import hashlib, os, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / "bin" / "python"
REQ = ROOT / "requirements.txt"
STAMP = VENV / ".reqs_installed.sha256"

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

def ensure_venv():
    if not PY.exists():
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    # upgrade pip
    subprocess.check_call([str(PY), "-m", "pip", "install", "-U", "pip"])

def ensure_requirements():
    want = sha256(REQ)
    have = STAMP.read_text().strip() if STAMP.exists() else ""
    if want != have:
        subprocess.check_call([str(PY), "-m", "pip", "install", "-r", str(REQ)])
        STAMP.write_text(want)

def ensure_fpcalc():
    # odpala nasz installer; jeśli Homebrew jest -> brew, inaczej pobierze vendor
    subprocess.check_call([str(PY), str(ROOT / "scripts" / "install_fpcalc.py")])

def run_tool(which: str):
    if which == "scan":
        script = ROOT / "scripts" / "scan_inbox.py"
    elif which == "apply":
        script = ROOT / "scripts" / "apply_decisions.py"
    else:
        print("Użycie: run.py [scan|apply]")
        sys.exit(2)
    subprocess.check_call([str(PY), str(script)])

def main():
    args = sys.argv[1:]
    if not args:
        print("Użycie: run.py [scan|apply]")
        sys.exit(2)
    ensure_venv()
    ensure_requirements()
    ensure_fpcalc()
    run_tool(args[0])

if __name__ == "__main__":
    main()
