"""Check if vendor wheels have newer versions available on GitHub.

Non-blocking: if GitHub is unreachable, exits 0 with a warning.
Intended to run as part of STEP 0 setup.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

VENDOR_DIR = Path(__file__).parent.parent / "vendor"

PACKAGES = [
    {
        "wheel_glob": "traktor_nml_utils-*.whl",
        "repo": "https://github.com/wolkenarchitekt/traktor-nml-utils.git",
        "name": "traktor-nml-utils",
    },
]

_VERSION_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?)")


def _wheel_version(glob: str) -> str | None:
    matches = sorted(VENDOR_DIR.glob(glob))
    if not matches:
        return None
    # traktor_nml_utils-3.3.0-py3-none-any.whl → 3.3.0
    m = _VERSION_RE.search(matches[-1].stem)
    return m.group(1) if m else None


def _latest_tag(repo: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--tags", "--sort=-v:refname", repo],
            capture_output=True, text=True, timeout=8,
        )
        for line in result.stdout.splitlines():
            ref = line.split("\t")[-1]
            if ref.startswith("refs/tags/") and "^{}" not in ref:
                tag = ref.removeprefix("refs/tags/").lstrip("v")
                m = _VERSION_RE.match(tag)
                if m:
                    return m.group(1)
    except Exception:
        pass
    return None


def main() -> None:
    any_outdated = False
    for pkg in PACKAGES:
        current = _wheel_version(pkg["wheel_glob"])
        if current is None:
            print(f"  [vendor] {pkg['name']}: wheel not found in vendor/")
            continue

        latest = _latest_tag(pkg["repo"])
        if latest is None:
            print(f"  [vendor] {pkg['name']} {current} (GitHub unreachable — skipping update check)")
            continue

        def _ver(s: str) -> tuple[int, ...]:
            return tuple(int(x) for x in s.split("."))

        if _ver(latest) > _ver(current):
            print(
                f"  [vendor] ⚠️  {pkg['name']}: vendor={current}, latest={latest} — "
                f"run: python scripts/update_vendor.py {pkg['name']}"
            )
            any_outdated = True
        elif _ver(current) > _ver(latest):
            print(f"  [vendor] {pkg['name']} {current} ✓ (ahead of latest tag {latest})")
        else:
            print(f"  [vendor] {pkg['name']} {current} ✓ up to date")

    sys.exit(0)  # always non-blocking


if __name__ == "__main__":
    main()
