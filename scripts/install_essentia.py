#!/usr/bin/env python3
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import platform


def run(cmd: list[str]) -> int:
    try:
        print("$", " ".join(cmd))
        return subprocess.call(cmd)
    except Exception as e:
        print(f"Error running {' '.join(cmd)}: {e}")
        return 1


def main() -> int:
    system = platform.system()
    if system != "Darwin":
        print("❌ This helper currently supports macOS (Homebrew) only.")
        print("   For other platforms, please install Essentia via conda:")
        print("   conda install -c conda-forge essentia")
        return 1

    brew = shutil.which("brew")
    if not brew:
        print("❌ Homebrew not found. Install from https://brew.sh/ and rerun.")
        return 1

    # Check if essentia installed
    print("🔎 Checking Essentia installation...")
    code = run([brew, "list", "essentia"])  # 0 if installed
    if code == 0:
        print("✅ Essentia already installed.")
        return 0

    print("⬇️ Installing Essentia via Homebrew...")
    code = run([brew, "install", "essentia"])
    if code != 0:
        print("❌ Failed to install Essentia. See output above.")
        return code

    print("✅ Essentia installed. You can verify with:")
    print("   python -m djlib.cli analyze-audio --check-env")
    return 0


if __name__ == "__main__":
    sys.exit(main())
