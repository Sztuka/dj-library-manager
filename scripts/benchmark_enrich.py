#!/usr/bin/env python3
"""
Benchmark script for enrich-online performance testing.

Usage:
    # Run benchmark on current unsorted.csv:
    python scripts/benchmark_enrich.py --run
    
    # Run benchmark with specific number of tracks (first N rows):
    python scripts/benchmark_enrich.py --run --limit 10
    
    # Run detailed per-phase benchmark (in-process, accurate cache stats):
    python scripts/benchmark_enrich.py --run --detailed --limit 5
    
    # Show results history:
    python scripts/benchmark_enrich.py --results

Results are stored in LOGS/benchmark_results.csv
Detailed results in LOGS/phase1_benchmark_detailed.csv
"""
from __future__ import annotations
import argparse
import csv
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

RESULTS_FILE = Path(__file__).parent.parent / "LOGS" / "benchmark_results.csv"
DETAILED_RESULTS_FILE = Path(__file__).parent.parent / "LOGS" / "phase1_benchmark_detailed.csv"


def get_git_info() -> tuple[str, str]:
    """Get current git branch and short commit hash."""
    try:
        branch = subprocess.check_output(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=Path(__file__).parent.parent,
            text=True
        ).strip()
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent.parent,
            text=True
        ).strip()
        return branch, commit
    except Exception:
        return "unknown", "unknown"


def count_pending_tracks() -> int:
    """Count tracks in unsorted.csv that need enrichment."""
    try:
        from djlib.unsorted import load_unsorted_rows
        from djlib.config import UNSORTED_CSV
        rows = load_unsorted_rows(UNSORTED_CSV)
        return len(rows)
    except Exception as e:
        print(f"Warning: Could not count tracks: {e}")
        return 0


def run_benchmark(limit: int | None = None, note: str = "") -> dict:
    """Run enrich-online and measure time."""
    from djlib.metadata import mb_client
    
    track_count = count_pending_tracks()
    if track_count == 0:
        print("❌ No tracks in unsorted.csv. Run scan first.")
        return {}
    
    if limit:
        track_count = min(track_count, limit)
    
    branch, commit = get_git_info()
    
    # Clear MB cache before benchmark for fair comparison (if available)
    if hasattr(mb_client, 'clear_mb_cache'):
        mb_client.clear_mb_cache()
    
    print(f"\n🚀 Running enrich benchmark")
    print(f"   Branch: {branch} ({commit})")
    print(f"   Tracks: {track_count}" + (f" (limited from {count_pending_tracks()})" if limit else ""))
    print()
    
    # Build command
    cmd = [sys.executable, "-m", "djlib.cli", "enrich-online"]
    if limit:
        cmd.extend(["--limit", str(limit)])
    
    print("🔍 Running enrich-online...")
    enrich_start = time.time()
    result = subprocess.run(
        cmd, 
        cwd=Path(__file__).parent.parent,
        capture_output=True,
        text=True
    )
    enrich_time = time.time() - enrich_start
    
    # Get cache stats (if available - only on branches with caching)
    total_hits = 0
    total_misses = 0
    if hasattr(mb_client, 'get_mb_cache_stats'):
        cache_stats = mb_client.get_mb_cache_stats()
        total_hits = sum(s.get("hits", 0) for s in cache_stats.values())
        total_misses = sum(s.get("misses", 0) for s in cache_stats.values())
    
    # Calculate stats
    time_per_track = enrich_time / track_count if track_count > 0 else 0
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "branch": branch,
        "commit": commit,
        "track_count": track_count,
        "enrich_time_s": round(enrich_time, 2),
        "time_per_track_s": round(time_per_track, 2),
        "cache_hits": total_hits,
        "cache_misses": total_misses,
        "note": note,
    }
    
    print(f"\n📊 Results:")
    print(f"   Total time: {enrich_time:.1f}s")
    print(f"   Time per track: {time_per_track:.1f}s")
    print(f"   Cache hits/misses: {total_hits}/{total_misses}")
    print(f"   Branch: {branch} ({commit})")
    
    # Save results
    save_results(results)
    
    return results


def run_detailed_benchmark(limit: int = 5, note: str = "") -> dict:
    """Run in-process benchmark with per-phase timing (accurate cache stats)."""
    from djlib.metadata import mb_client
    from djlib.enrich import enrich_online_for_row
    from djlib.unsorted import load_unsorted_rows
    from djlib.config import UNSORTED_CSV
    
    rows = load_unsorted_rows(UNSORTED_CSV)
    if not rows:
        print("❌ No unsorted.csv found or empty. Run scan first.")
        return {}
    
    # Limit rows
    rows = rows[:limit]
    
    track_count = len(rows)
    if track_count == 0:
        print("❌ No tracks found.")
        return {}
    
    branch, commit = get_git_info()
    
    # Clear cache before benchmark
    if hasattr(mb_client, 'clear_mb_cache'):
        mb_client.clear_mb_cache()
    
    print(f"\n🚀 Running DETAILED benchmark (in-process, with phase timing)")
    print(f"   Branch: {branch} ({commit})")
    print(f"   Tracks: {track_count}")
    print()
    
    # Time each track
    total_start = time.time()
    phase_times = {
        "acoustid": 0.0,
        "mb_search": 0.0,
        "genre_resolver": 0.0,
        "archive_org": 0.0,
        "other": 0.0,
    }
    
    for i, row in enumerate(rows, 1):
        path_str = row.get("file_path", "") or row.get("path", "")
        path = Path(path_str) if path_str else None
        if not path or not path.exists():
            print(f"   [{i}/{track_count}] ⚠️  File not found: {path_str[:50] if path_str else '(empty)'}")
            continue
        
        print(f"   [{i}/{track_count}] Processing: {path.name[:50]}...")
        t0 = time.time()
        result = enrich_online_for_row(path, row)
        elapsed = time.time() - t0
        phase_times["other"] += elapsed
        print(f"             → {elapsed:.1f}s")
    
    total_time = time.time() - total_start
    
    # Get cache stats
    total_hits = 0
    total_misses = 0
    cache_details = {}
    if hasattr(mb_client, 'get_mb_cache_stats'):
        cache_stats = mb_client.get_mb_cache_stats()
        total_hits = sum(s.get("hits", 0) for s in cache_stats.values())
        total_misses = sum(s.get("misses", 0) for s in cache_stats.values())
        cache_details = cache_stats
    
    time_per_track = total_time / track_count if track_count > 0 else 0
    
    results = {
        "timestamp": datetime.now().isoformat(),
        "branch": branch,
        "commit": commit,
        "track_count": track_count,
        "enrich_time_s": round(total_time, 2),
        "time_per_track_s": round(time_per_track, 2),
        "cache_hits": total_hits,
        "cache_misses": total_misses,
        "note": note,
    }
    
    print(f"\n📊 Detailed Results:")
    print(f"   Total time: {total_time:.1f}s")
    print(f"   Time per track: {time_per_track:.1f}s")
    print(f"   Cache hits/misses: {total_hits}/{total_misses}")
    
    if cache_details:
        print(f"\n   Cache breakdown:")
        for name, stats in cache_details.items():
            hits = stats.get("hits", 0)
            misses = stats.get("misses", 0)
            if hits or misses:
                print(f"     {name}: {hits} hits / {misses} misses")
    
    # Save to detailed CSV
    save_detailed_results(results, cache_details)
    # Also save to standard results
    save_results(results)
    
    return results


def save_detailed_results(results: dict, cache_details: dict) -> None:
    """Save detailed results with cache breakdown."""
    DETAILED_RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    # Build row with cache details
    row = {
        "test_id": int(time.time()),
        "date": datetime.now().strftime("%Y-%m-%d"),
        "branch": results.get("branch", ""),
        "commit": results.get("commit", ""),
        "optimization": results.get("note", ""),
        "tracks": results.get("track_count", 0),
        "total_time_s": results.get("enrich_time_s", 0),
        "per_track_s": results.get("time_per_track_s", 0),
        "cache_hits": results.get("cache_hits", 0),
        "cache_misses": results.get("cache_misses", 0),
    }
    
    # Add per-cache stats
    for name, stats in cache_details.items():
        row[f"cache_{name}_hits"] = stats.get("hits", 0)
        row[f"cache_{name}_misses"] = stats.get("misses", 0)
    
    file_exists = DETAILED_RESULTS_FILE.exists()
    
    # Read existing headers if file exists
    if file_exists:
        with open(DETAILED_RESULTS_FILE, "r") as f:
            reader = csv.DictReader(f)
            existing_headers = reader.fieldnames or []
        # Merge headers
        all_headers = list(existing_headers)
        for key in row.keys():
            if key not in all_headers:
                all_headers.append(key)
    else:
        all_headers = list(row.keys())
    
    # Rewrite file with updated headers if needed
    if file_exists:
        with open(DETAILED_RESULTS_FILE, "r") as f:
            existing_rows = list(csv.DictReader(f))
        with open(DETAILED_RESULTS_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_headers)
            writer.writeheader()
            for existing_row in existing_rows:
                writer.writerow(existing_row)
            writer.writerow(row)
    else:
        with open(DETAILED_RESULTS_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=all_headers)
            writer.writeheader()
            writer.writerow(row)
    
    print(f"\n💾 Detailed results saved to {DETAILED_RESULTS_FILE}")


def save_results(results: dict) -> None:
    """Append results to CSV file."""
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    file_exists = RESULTS_FILE.exists()
    
    with open(RESULTS_FILE, "a", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=results.keys())
        if not file_exists:
            writer.writeheader()
        writer.writerow(results)
    
    print(f"\n💾 Results saved to {RESULTS_FILE}")


def show_results() -> None:
    """Display benchmark results history."""
    if not RESULTS_FILE.exists():
        print("❌ No benchmark results yet. Run with --run first.")
        return
    
    print("\n📊 Benchmark Results History")
    print("=" * 110)
    print(f"{'Timestamp':<20} {'Branch':<22} {'Commit':<8} {'Tracks':<7} {'Time(s)':<9} {'Per Track':<10} {'Cache H/M':<12} {'Note':<15}")
    print("-" * 110)
    
    with open(RESULTS_FILE, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp", "")[:16]
            branch = row.get("branch", "")[:21]
            commit = row.get("commit", "")[:7]
            tracks = row.get("track_count", "")
            total = row.get("enrich_time_s", "")
            per_track = row.get("time_per_track_s", "")
            hits = row.get("cache_hits", "0")
            misses = row.get("cache_misses", "0")
            note = row.get("note", "")[:14]
            
            print(f"{ts:<20} {branch:<22} {commit:<8} {tracks:<7} {total:<9} {per_track:<10} {hits}/{misses:<10} {note:<15}")
    
    print("=" * 110)


def main():
    parser = argparse.ArgumentParser(description="Benchmark enrich-online performance")
    parser.add_argument("--run", action="store_true", help="Run benchmark")
    parser.add_argument("--detailed", action="store_true", help="Run detailed in-process benchmark with cache stats")
    parser.add_argument("--limit", type=int, help="Limit to first N tracks")
    parser.add_argument("--note", type=str, default="", help="Add note to result")
    parser.add_argument("--results", action="store_true", help="Show results history")
    
    args = parser.parse_args()
    
    if args.run:
        if args.detailed:
            run_detailed_benchmark(limit=args.limit or 5, note=args.note)
        else:
            run_benchmark(limit=args.limit, note=args.note)
    elif args.results:
        show_results()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
