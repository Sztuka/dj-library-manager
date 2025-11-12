from __future__ import annotations

# Public API for the audio analysis package (skeleton)

ALGO_VERSION = 2

try:
    from .essentia_backend import check_env, analyze  # noqa: F401
except Exception:  # pragma: no cover
    # Keep package importable even if backend has missing deps at import time
    def check_env() -> dict:
        return {"essentia_available": False, "details": "backend not imported"}

    def analyze(*_, **__):
        raise RuntimeError("Audio backend unavailable. Install Essentia or use --check-env.")

__all__ = [
    "ALGO_VERSION",
    "check_env",
    "analyze",
]
