#!/usr/bin/env python3
"""
Test script for adding raw entries to Traktor collection.nml

This tests different approaches to see what Traktor accepts:
1. Entry without AUDIO_ID (None) - attribute not present in XML
2. Entry with empty AUDIO_ID ("") - AUDIO_ID="" in XML

The test uses a copy of collection.nml so we don't break the real one.
"""

import shutil
import tempfile
from pathlib import Path
from datetime import datetime

# Add project root to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from traktor_nml_utils import TraktorCollection
from traktor_nml_utils.models.collection import Entrytype, Locationtype, Infotype, Tempotype


def get_collection_path() -> Path:
    """Get the Traktor collection.nml path."""
    return Path.home() / "Documents" / "Native Instruments" / "Traktor 3.11.1" / "collection.nml"


def count_entries(collection: TraktorCollection) -> int:
    """Count entries in collection."""
    return len(collection.nml.collection.entry)


def create_test_entry(
    collection: TraktorCollection,
    title: str,
    artist: str,
    file_path: str,
    audio_id: str | None = None,
) -> None:
    """
    Create a test entry in the collection.
    
    Args:
        collection: TraktorCollection instance
        title: Track title
        artist: Track artist
        file_path: Full path to the (fake) audio file
        audio_id: AUDIO_ID value (None = omit, "" = empty, or actual value)
    """
    # Parse the path into Traktor format
    path = Path(file_path)
    
    # Traktor uses /:dir/:subdir/: format
    dir_parts = []
    for part in path.parent.parts:
        if part == "/":
            continue
        dir_parts.append(part)
    
    traktor_dir = "/:" + "/:".join(dir_parts) + "/:"
    traktor_file = path.name
    volume = "Macintosh HD"
    
    # Create entry using Entrytype directly
    entry = Entrytype(
        title=title,
        artist=artist,
        audio_id=audio_id,  # None = omit, "" = empty string
        location=Locationtype(
            dir=traktor_dir,
            file=traktor_file,
            volume=volume,
            volumeid=volume,
        ),
        info=Infotype(
            bitrate=320000,
            playtime=300,
            import_date=datetime.now().strftime("%Y/%m/%d"),
        ),
        tempo=Tempotype(bpm=125.0),
    )
    
    # Add to collection
    collection.nml.collection.entry.append(entry)
    collection.nml.collection.entries = len(collection.nml.collection.entry)
    
    print(f"  Added: {artist} - {title}")
    print(f"    audio_id={repr(audio_id)}")


def test_traktor_entries():
    """Test adding entries with different AUDIO_ID approaches."""
    
    original_path = get_collection_path()
    if not original_path.exists():
        print(f"ERROR: Collection not found at {original_path}")
        return
    
    # Create a temporary copy
    with tempfile.TemporaryDirectory() as tmpdir:
        test_path = Path(tmpdir) / "collection_test.nml"
        shutil.copy(original_path, test_path)
        
        print(f"Testing with copy: {test_path}")
        print(f"Original size: {original_path.stat().st_size} bytes")
        
        # Load collection
        collection = TraktorCollection(path=test_path)
        initial_count = count_entries(collection)
        print(f"Initial entry count: {initial_count}")
        
        # Test entries - all pointing to fake paths (files don't need to exist)
        fake_base = "/Users/sztuka/Desktop/MUSIC/TEST_ENTRIES"
        
        print("\n=== Test 1: audio_id=None (omit attribute) ===")
        create_test_entry(
            collection,
            title="Test Track No AudioID",
            artist="Test Artist 1",
            file_path=f"{fake_base}/test_no_audioid.mp3",
            audio_id=None,
        )
        
        print("\n=== Test 2: audio_id='' (empty string) ===")
        create_test_entry(
            collection,
            title="Test Track Empty AudioID",
            artist="Test Artist 2",
            file_path=f"{fake_base}/test_empty_audioid.mp3",
            audio_id="",
        )
        
        # Save the collection
        print("\n=== Saving collection ===")
        collection.save()
        
        final_count = count_entries(collection)
        print(f"Final entry count: {final_count}")
        print(f"Added {final_count - initial_count} entries")
        print(f"Test file size: {test_path.stat().st_size} bytes")
        
        # Show the added entries in the XML
        print("\n=== Inspecting added entries in XML ===")
        with open(test_path, "r") as f:
            content = f.read()
        
        for test_title in ["Test Track No AudioID", "Test Track Empty AudioID"]:
            if test_title in content:
                # Find the entry
                start = content.find(f'TITLE="{test_title}"')
                if start > 0:
                    # Go back to find ENTRY
                    entry_start = content.rfind("<ENTRY", 0, start)
                    entry_end = content.find("</ENTRY>", start) + 8
                    entry_xml = content[entry_start:entry_end]
                    print(f"\n{test_title}:")
                    print(f"  {entry_xml[:300]}...")
        
        # Now test if we can actually apply this to the real collection
        print("\n" + "="*60)
        print("To test with REAL Traktor, run with --apply flag")
        print("This will add test entries to real collection")
        print("="*60)


def apply_test_to_real_collection():
    """Apply test entries to the real collection."""
    
    collection_path = get_collection_path()
    if not collection_path.exists():
        print(f"ERROR: Collection not found at {collection_path}")
        return
    
    # Backup first
    backup_path = collection_path.with_suffix(
        f".nml.test-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    )
    shutil.copy(collection_path, backup_path)
    print(f"Backup created: {backup_path}")
    
    # Load and modify
    collection = TraktorCollection(path=collection_path)
    initial_count = count_entries(collection)
    print(f"Initial entry count: {initial_count}")
    
    # Add test entries
    fake_base = "/Users/sztuka/Desktop/MUSIC/TEST_ENTRIES"
    timestamp = datetime.now().strftime("%H%M%S")
    
    print("\n=== Adding test entry with audio_id=None ===")
    create_test_entry(
        collection,
        title=f"DJLIB TEST {timestamp} - No AudioID",
        artist="DJLIB Test",
        file_path=f"{fake_base}/test_none_{timestamp}.mp3",
        audio_id=None,
    )
    
    print("\n=== Adding test entry with audio_id='' ===")
    create_test_entry(
        collection,
        title=f"DJLIB TEST {timestamp} - Empty AudioID",
        artist="DJLIB Test",
        file_path=f"{fake_base}/test_empty_{timestamp}.mp3",
        audio_id="",
    )
    
    # Save
    collection.save()
    
    final_count = count_entries(collection)
    print(f"\nFinal entry count: {final_count}")
    print(f"Added {final_count - initial_count} entries")
    print(f"\nNow open Traktor and check if:")
    print("1. Collection loads without errors")
    print("2. Test entries appear in the list")
    print("3. What happens when you try to analyze them")
    print(f"\nTo restore: cp '{backup_path}' '{collection_path}'")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Test Traktor entry formats")
    parser.add_argument("--apply", action="store_true", 
                       help="Apply test entry to REAL collection (creates backup)")
    args = parser.parse_args()
    
    if args.apply:
        apply_test_to_real_collection()
    else:
        test_traktor_entries()
