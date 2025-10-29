#!/usr/bin/env python3
from __future__ import annotations
import sys, yaml, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
tx = yaml.safe_load((ROOT/"taxonomy.yml").read_text("utf-8"))

bad = []
for key in ("ready_buckets", "review_buckets"):
    for s in tx.get(key, []):
        if "_" in s:
            bad.append(f"Underscore in name: {s}")
        if s.strip() == "MIXES/":
            bad.append("MIXES must not end with slash (use 'MIXES').")

if bad:
    print("❌ Taxonomy errors:\n- " + "\n- ".join(bad))
    sys.exit(1)

print("✅ taxonomy.yml OK")
