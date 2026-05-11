"""Audio format conversion helpers.

Converts WAV and FLAC to AIFF so Rekordbox can read ID3 tags and display
cover art. Uses ffmpeg (must be on PATH). Output preserves original bit depth
and sample rate — ffmpeg selects the correct PCM codec automatically.
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CONVERT_EXTS = {".wav", ".flac"}


def needs_conversion(path: Path) -> bool:
    """Return True if the file should be converted to AIFF before export."""
    return path.suffix.lower() in _CONVERT_EXTS


def audio_sha256(path: Path) -> Optional[str]:
    """Return SHA256 of the decoded audio stream (container/tag-agnostic).

    Uses ffmpeg to decode the audio to raw PCM and hash the samples.
    Identical audio in different containers (WAV vs AIFF) produces the same hash.
    Returns None if ffmpeg fails or file is unreadable.
    """
    cmd = [
        "ffmpeg",
        "-i", str(path),
        "-vn",          # ignore video/cover streams
        "-f", "hash",
        "-hash", "SHA256",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        for line in result.stdout.splitlines():
            if line.startswith("SHA256="):
                return line.split("=", 1)[1].strip()
        log.warning("audio_sha256: no hash in ffmpeg output for %s", path.name)
        return None
    except FileNotFoundError:
        log.warning("ffmpeg not found on PATH — cannot hash %s", path.name)
        return None
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg timed out hashing %s", path.name)
        return None
    except Exception as exc:
        log.warning("audio_sha256 unexpected error for %s: %s", path.name, exc)
        return None


def convert_to_aiff(src: Path) -> Optional[Path]:
    """Convert src (WAV or FLAC) to a temporary AIFF file.

    Returns the Path to the temp file on success, or None if ffmpeg fails.
    Caller is responsible for deleting the temp file after use.

    Bit depth and sample rate are preserved — ffmpeg auto-selects the
    appropriate PCM codec (pcm_s16be for 16-bit, pcm_s24be for 24-bit, etc.).
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".aiff", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    cmd = [
        "ffmpeg",
        "-y",           # overwrite without asking
        "-i", str(src),
        "-vn",          # drop video/cover art streams (re-embedded by write_tags)
        str(tmp_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            log.warning(
                "ffmpeg conversion failed for %s (exit %d):\n%s",
                src.name, result.returncode, result.stderr[-500:],
            )
            tmp_path.unlink(missing_ok=True)
            return None
        return tmp_path
    except FileNotFoundError:
        log.warning("ffmpeg not found on PATH — cannot convert %s to AIFF", src.name)
        tmp_path.unlink(missing_ok=True)
        return None
    except subprocess.TimeoutExpired:
        log.warning("ffmpeg timed out converting %s", src.name)
        tmp_path.unlink(missing_ok=True)
        return None
    except Exception as exc:
        log.warning("unexpected error converting %s: %s", src.name, exc)
        tmp_path.unlink(missing_ok=True)
        return None
