#!/usr/bin/env python3
from __future__ import annotations
from djlib.csvdb import load_records, save_records
from djlib.config import CSV_PATH
from djlib.placement import decide_bucket

HARDCOMMIT_CONF = 0.85   # ustaw od razu target_subfolder
SUGGEST_CONF    = 0.65   # poniżej hardcommit → tylko ai_guess_*

def main() -> None:
    rows = load_records(CSV_PATH)
    set_cnt = sug_cnt = 0
    for r in rows:
        if r.get("target_subfolder"):
            continue
        tgt, conf, reason = decide_bucket(r)
        if not tgt:
            continue
        if conf >= HARDCOMMIT_CONF:
            r["target_subfolder"] = f"READY TO PLAY/{tgt}"
            r["ai_guess_bucket"] = ""
            r["ai_guess_comment"] = f"rule:{reason}; conf={conf:.2f}"
            set_cnt += 1
        elif conf >= SUGGEST_CONF:
            r["ai_guess_bucket"]  = f"READY TO PLAY/{tgt}"
            r["ai_guess_comment"] = f"rule:{reason}; conf={conf:.2f}"
            sug_cnt += 1
    if set_cnt or sug_cnt:
        save_records(CSV_PATH, rows)
    print(f"✅ Auto-decide: set={set_cnt}, suggested={sug_cnt}")

if __name__ == "__main__":
    main()
