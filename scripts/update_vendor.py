"""Rebuild a vendor wheel from its GitHub source.

Usage:
    python scripts/update_vendor.py traktor-nml-utils
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).parent.parent / "vendor"

PACKAGES = {
    "traktor-nml-utils": {
        "repo": "git+https://github.com/wolkenarchitekt/traktor-nml-utils.git",
        "wheel_glob": "traktor_nml_utils-*.whl",
    },
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in PACKAGES:
        print(f"Usage: python scripts/update_vendor.py <package>")
        print(f"Available: {', '.join(PACKAGES)}")
        sys.exit(1)

    name = sys.argv[1]
    pkg = PACKAGES[name]

    print(f"Building wheel for {name} from {pkg['repo']} …")
    VENDOR_DIR.mkdir(exist_ok=True)

    # Remove old wheels
    for old in VENDOR_DIR.glob(pkg["wheel_glob"]):
        old.unlink()
        print(f"  Removed {old.name}")

    pip = Path(sys.executable).parent / "pip"
    result = subprocess.run(
        [str(pip), "wheel", "--no-deps", "--wheel-dir", str(VENDOR_DIR), pkg["repo"]],
    )
    if result.returncode != 0:
        print("ERROR: wheel build failed.")
        sys.exit(1)

    wheels = sorted(VENDOR_DIR.glob(pkg["wheel_glob"]))
    if wheels:
        print(f"  Built: {wheels[-1].name}")
        print(f"\nNow update requirements.txt to point at the new wheel, then commit vendor/.")
    else:
        print("ERROR: no wheel produced.")
        sys.exit(1)


if __name__ == "__main__":
    main()
