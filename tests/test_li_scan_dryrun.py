import shutil
from pathlib import Path
import subprocess
import sys
import yaml

def run_cli(*args, cwd):
    cmd = [sys.executable, "-m", "djlib.cli", *args]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)

def test_scan_and_apply_dryrun(tmp_path, monkeypatch):
    lib = tmp_path / "LIB"
    inbox = tmp_path / "INBOX"
    lib.mkdir(); inbox.mkdir()

    # Podstawowy config + tax
    (tmp_path / "config.yml").write_text(yaml.safe_dump({
        "LIB_ROOT": str(lib),
        "INBOX_UNSORTED": str(inbox),
        "CSV_PATH": str(tmp_path / "library.csv"),
    }, allow_unicode=True, sort_keys=False), "utf-8")

    (tmp_path / "taxonomy.yml").write_text(yaml.safe_dump({
        "ready_buckets": ["CLUB/HOUSE", "MIXES"],
        "review_buckets": ["UNDECIDED", "NEEDS EDIT"],
    }, allow_unicode=True, sort_keys=False), "utf-8")

    # Udaj plik audio (pusta atrapa – skan powinien go zobaczyć jako plik)
    dummy = inbox / "track.mp3"
    dummy.write_bytes(b"\x00\x00\x00")

    # scan
    r1 = run_cli("scan", cwd=tmp_path)
    assert r1.returncode == 0

    # dry-run (nie powinien rzucać wyjątków)
    r2 = run_cli("apply", "--dry-run", cwd=tmp_path)
    assert r2.returncode == 0
