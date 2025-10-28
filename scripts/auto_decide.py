#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, List
import argparse
import yaml

from djlib.config import CSV_PATH
from djlib.csvdb import load_records, save_records
from djlib.buckets import is_valid_target
# kolumny: artist, title, genre, comment, ai_guess_bucket, target_subfolder

def load_rules(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"rules": [], "fallbacks": {}}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {"rules": [], "fallbacks": {}}

def decide_for_row(row: Dict[str, str], rules: Dict[str, Any]) -> str:
    artist = (row.get("artist") or "").lower()
    title  = (row.get("title") or "").lower()
    genre  = (row.get("genre") or "").lower()
    comm   = (row.get("ai_guess_comment") or row.get("comment") or "").lower()

    haystack = " ".join([artist, title, genre, comm])

    for rule in rules.get("rules", []):
        words = [w.lower() for w in rule.get("contains", [])]
        if any(w in haystack for w in words):
            target = rule.get("target", "")
            return target

    # fallbacks
    guess = row.get("ai_guess_bucket") or ""
    fb = rules.get("fallbacks", {})
    if guess in fb:
        return fb[guess]
    return fb.get("default", "REVIEW QUEUE/UNDECIDED")

def main():
    ap = argparse.ArgumentParser(description="Auto-uzupełnianie target_subfolder na podstawie rules.yml")
    ap.add_argument("--rules", default="rules.yml", help="Ścieżka do pliku rules.yml")
    ap.add_argument("--only-empty", action="store_true", help="Uzupełniaj tylko tam, gdzie target_subfolder jest pusty")
    args = ap.parse_args()

    rules = load_rules(Path(args.rules))
    rows = load_records(CSV_PATH)
    updated = 0

    for r in rows:
        if args.only_empty and (r.get("target_subfolder") or "").strip():
            continue
        proposal = decide_for_row(r, rules)
        if is_valid_target(proposal):
            r["target_subfolder"] = proposal
            updated += 1

    if updated:
        save_records(CSV_PATH, rows)
        print(f"Zaktualizowano {updated} wierszy.")
    else:
        print("Brak zmian.")

if __name__ == "__main__":
    main()
