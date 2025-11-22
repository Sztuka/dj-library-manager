# Installation

This project supports two paths for audio analysis (Essentia):

- macOS with Homebrew (recommended on macOS)
- Cross‑platform Conda environment (conda‑forge)

Essentia is optional. The app works without it; BPM/Key/Energy detection will be skipped. When present, analysis results are cached.

Backend modes:

- Python bindings (preferred with Conda): full in-process analysis
- CLI fallback (works with Homebrew binary): uses `essentia_streaming_extractor_music`/`streaming_extractor_music` to extract features and parse JSON
- Docker fallback (cross‑platform): runs the Linux CLI extractor in a container, no local install required

## 1) Python venv (base app)

Create a virtualenv and install Python requirements:

- VS Code task: "STEP 0 — Setup: create venv & install deps"
- Or manually:
  - python3 -m venv .venv
  - source .venv/bin/activate
  - pip install -U pip
  - pip install -r requirements.txt
  - pip install -e .

## 2) Install Essentia

### macOS (Homebrew)

- Preferred: use the VS Code task "TOOLS — Install Essentia (Homebrew)". It wraps `scripts/install_essentia.py` and will attempt `brew install essentia`.
- Alternatively, run:
  - brew install essentia

Note: If Python bindings don’t import in your venv (e.g., Python 3.13), the CLI fallback will still work as long as the extractor binary is installed and on PATH. We auto-detect both `essentia_streaming_extractor_music` and `streaming_extractor_music`.

After installing, verify:

- VS Code task: "TOOLS — Check audio env"
- Or: `.venv/bin/python -m djlib.cli analyze-audio --check-env`

Expected output fields include:

- `essentia_available`: Python bindings importable
- `essentia_cli_available`: extractor binary found
- `cli_binary`: path to the binary if available
- `essentia_docker_available`: Docker detected on your system
- `essentia_docker_enabled`: Whether Docker fallback is enabled via env var
- `docker_image`: Image name configured for Docker fallback

### Cross‑platform (Conda)

If you prefer an isolated environment with Essentia preinstalled:

- Install Miniforge/Mambaforge (recommended)
- Create the environment from `environment.yml`:
  - mamba env create -f environment.yml # or: conda env create -f environment.yml
  - conda activate djlib
  - pip install -e . # install the local package into the conda env

Note: `environment.yml` installs `essentia`, `ffmpeg`, `chromaprint`, and your base Python deps (via pip -r requirements.txt). This path is the most reliable for Python bindings.

### macOS/Linux/Windows (Docker fallback)

If Homebrew/Conda paths are inconvenient, use Docker to run the streaming extractor:

1. Build the image (one‑time):

```zsh
docker build -t djlib-essentia:local -f docker/Dockerfile.essentia docker
```

2. Enable Docker fallback for analyze‑audio and set the image name (in your shell):

```zsh
export DJLIB_ESSENTIA_DOCKER=1
export DJLIB_ESSENTIA_IMAGE=djlib-essentia:local
```

3. Verify:

```zsh
./.venv/bin/python -m djlib.cli analyze-audio --check-env
```

You should see `essentia_docker_available: true`, `essentia_docker_enabled: true`, and `docker_image: djlib-essentia:local`.

With Docker fallback enabled, the backend automatically runs:

```text
docker run --rm -v <input_audio>:/in/audio:ro -v <tmp_dir>:/out djlib-essentia:local /in/audio /out/features.json
```

No local Essentia install is needed on the host.

## 3) Run audio analysis

- Check env: "TOOLS — Check audio env"
- Analyze and cache features: "WORKFLOW 2 — Analyze audio (Essentia)"
- Generate preview CSV with detected columns:
  - python scripts/report_preview.py

Analysis results are cached in a SQLite DB. Re‑runs are fast unless you pass `--recompute`.

## 4) Optional pip extra

`pyproject.toml` exposes an optional extra for Essentia:

- pip install -e .[audio]

This may work on some platforms where wheels are available, but Homebrew/Conda are preferred for reliability.

## Troubleshooting

- Essentia not found

  - Ensure `essentia` binaries/libraries are installed and visible (brew/conda)
  - Reopen terminal/VS Code window so PATH/LD paths are refreshed
  - Use "TOOLS — Check audio env" to see details

- fpcalc/chromaprint

  - Use the existing tasks: "TOOLS — Install fpcalc (Homebrew)" or vendor script

- Conflicting Python envs
  - Confirm which interpreter you’re using in VS Code (bottom‑left status bar)
  - `which python`, `which pip`, `python -c "import sys; print(sys.executable)"`
