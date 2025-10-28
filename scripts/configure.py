#!/usr/bin/env python3
from __future__ import annotations
from djlib.config import reconfigure, ensure_base_dirs, CONFIG_FILE

def main():
    cfg, path = reconfigure()
    ensure_base_dirs()
    print(f"\n✅ Zapisano konfigurację do: {path}")
    print(f"   library_root: {cfg.library_root}")
    print(f"   inbox_dir:    {cfg.inbox_dir}\n")

if __name__ == "__main__":
    main()
