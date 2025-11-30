#!/usr/bin/env python3
import argparse
import csv
import sys
from pathlib import Path
import traceback

from mutagen._file import File

DEFAULT_ROOT = Path.home() / 'Desktop' / 'MUSIC'
DEFAULT_OUTPUT = Path('data/OLD_LIBRARY_SOURCE.csv')

HEADER = ['artist', 'title', 'year', 'bpm', 'key', 'path', 'genre']

AUDIO_EXTS = {
    '.mp3',
    '.m4a',
    '.mp4',
    '.flac',
    '.wav',
    '.aiff',
    '.aif',
    '.aifc',
    '.ogg',
    '.oga',
    '.wma',
    '.aac',
}


def get_tag(file_obj, tag):
    if not file_obj or not file_obj.tags:
        return ''
    value = file_obj.tags.get(tag)
    if isinstance(value, list) and value:
        value = value[0]
    if value is None:
        return ''
    return str(value)


def iter_audio_files(root):
    for path in sorted(root.rglob('*')):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
            yield path


def write_csv(root: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', newline='') as out:
        writer = csv.writer(out)
        writer.writerow(HEADER)
        for path in iter_audio_files(root):
            try:
                audio = File(path, easy=True)
                artist = get_tag(audio, 'artist')
                title = get_tag(audio, 'title')
                year = get_tag(audio, 'date') or get_tag(audio, 'year')
                bpm = get_tag(audio, 'bpm')
                key = get_tag(audio, 'initialkey') or get_tag(audio, 'key')
                genre = get_tag(audio, 'genre')
                writer.writerow([artist, title, year, bpm, key, str(path), genre])
            except Exception:
                writer.writerow(['', '', '', '', '', str(path), ''])
                sys.stderr.write(f'Error reading tags: {path}\n{traceback.format_exc()}')


def parse_args():
    parser = argparse.ArgumentParser(
        description='Extract basic audio tags to CSV from a music directory.'
    )
    parser.add_argument(
        '--root',
        type=Path,
        default=DEFAULT_ROOT,
        help='Root directory with music files (recursively scanned).',
    )
    parser.add_argument(
        '--output',
        type=Path,
        default=DEFAULT_OUTPUT,
        help='Where to write the CSV output.',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.root.exists():
        sys.stderr.write(f'Root directory does not exist: {args.root}\n')
        sys.exit(1)
    write_csv(args.root, args.output)


if __name__ == '__main__':
    main()
