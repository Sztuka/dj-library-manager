"""Tests that fpcalc subprocess calls in fingerprint_info() never hang forever."""

from __future__ import annotations

import stat
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from djlib import fingerprint


def _force_acoustid_fallback():
    """acoustid.fingerprint_file() must fail so fingerprint_info() falls
    through to the fpcalc subprocess path we're testing."""
    return patch.object(
        fingerprint.acoustid,
        "fingerprint_file",
        side_effect=RuntimeError("no acoustid"),
    )


def test_fingerprint_info_calls_fpcalc_with_timeout(tmp_path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake audio")

    with (
        _force_acoustid_fallback(),
        patch(
            "djlib.fingerprint.ensure_fpcalc_in_env",
            return_value=Path("/usr/bin/fpcalc"),
        ),
        patch("djlib.fingerprint.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"duration": 1, "fingerprint": "abc"}',
            stderr="",
        )
        fingerprint.fingerprint_info(audio)

    assert mock_run.called
    _, kwargs = mock_run.call_args
    assert kwargs.get("timeout") is not None


def test_fingerprint_info_gives_up_on_hung_fpcalc(tmp_path, monkeypatch):
    fake_fpcalc = tmp_path / "fpcalc"
    fake_fpcalc.write_text("#!/bin/sh\nsleep 5\n")
    fake_fpcalc.chmod(fake_fpcalc.stat().st_mode | stat.S_IEXEC)

    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake audio")

    monkeypatch.setenv("DJLIB_FPCALC_TIMEOUT", "1")

    with (
        _force_acoustid_fallback(),
        patch("djlib.fingerprint.ensure_fpcalc_in_env", return_value=fake_fpcalc),
    ):
        start = time.monotonic()
        with pytest.raises(RuntimeError):
            fingerprint.fingerprint_info(audio)
        elapsed = time.monotonic() - start

    assert elapsed < 3


def test_fingerprint_info_timeout_returns_empty_fingerprint(tmp_path):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake audio")

    with (
        _force_acoustid_fallback(),
        patch(
            "djlib.fingerprint.ensure_fpcalc_in_env",
            return_value=Path("/usr/bin/fpcalc"),
        ),
        patch(
            "djlib.fingerprint.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="fpcalc", timeout=120),
        ),
    ):
        with pytest.raises(RuntimeError):
            fingerprint.fingerprint_info(audio)


def test_fpcalc_timeout_configurable_via_env(tmp_path, monkeypatch):
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"fake audio")

    monkeypatch.setenv("DJLIB_FPCALC_TIMEOUT", "7")

    with (
        _force_acoustid_fallback(),
        patch(
            "djlib.fingerprint.ensure_fpcalc_in_env",
            return_value=Path("/usr/bin/fpcalc"),
        ),
        patch("djlib.fingerprint.subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout='{"duration": 1, "fingerprint": "abc"}',
            stderr="",
        )
        fingerprint.fingerprint_info(audio)

    _, kwargs = mock_run.call_args
    assert kwargs.get("timeout") == 7
