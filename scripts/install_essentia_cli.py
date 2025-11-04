#!/usr/bin/env python3
"""
Attempt to help install Essentia CLI extractor on macOS.

This script:
- Checks if 'essentia_streaming_extractor_music' or 'streaming_extractor_music' is available on PATH
- If not, tries to detect a vendored binary in repo bin/mac
- If still missing, prints actionable instructions:
  * Option A: Homebrew tap (may require up-to-date Xcode and build from source)
  * Option B: Manual download of extractor binary and placing it in bin/mac/

We avoid hardcoding download URLs; please visit Essentia releases page:
  https://github.com/MTG/essentia/releases
and download a build that contains 'streaming_extractor_music' for macOS.

After placing the binary into 'bin/mac/', re-run 'TOOLS — Check audio env'.
"""
from __future__ import annotations
import os, shutil
from pathlib import Path

NAMES = ("essentia_streaming_extractor_music", "streaming_extractor_music")

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN_MAC = REPO_ROOT / "bin" / "mac"


def find_on_path() -> str | None:
    for n in NAMES:
        p = shutil.which(n)
        if p:
            return p
    return None


def find_vendored() -> str | None:
    for n in NAMES:
        p = BIN_MAC / n
        if p.exists() and p.is_file():
            return str(p)
    return None


def main() -> None:
    on_path = find_on_path()
    if on_path:
        print(f"✅ Found extractor on PATH: {on_path}")
        return
    vendored = find_vendored()
    if vendored:
        print(f"✅ Found vendored extractor: {vendored}")
        return
    print("\n❌ Essentia extractor not found.")
    print("\nOptions:")
    print("1) Homebrew (may require recent Xcode; builds from source):")
    print("   brew tap mtg/essentia")
    print("   brew install --HEAD mtg/essentia/essentia")
    print("   # If brew is not on PATH, use /opt/homebrew/bin/brew on Apple Silicon.")
    print("\n2) Manual download (recommended on Apple Silicon without Xcode):")
    print("   - Download a macOS build containing 'streaming_extractor_music' from:")
    print("     https://github.com/MTG/essentia/releases")
    print("   - Place the binary in:")
    print(f"     {BIN_MAC}")
    print("   - Ensure it's executable: chmod +x <binary>")
    print("\nThen re-run: .venv/bin/python -m djlib.cli analyze-audio --check-env")

if __name__ == "__main__":
    main()
