# Installation

This project supports two paths for audio analysis (Essentia):

- macOS with Homebrew (recommended on macOS)
- Cross‑platform Conda environment (conda‑forge)

Essentia is optional. The app works without it; BPM/Key/Energy detection will be skipped. When present, analysis results are cached.

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

After installing, verify:

- VS Code task: "TOOLS — Check audio env"
- Or: `.venv/bin/python -m djlib.cli analyze-audio --check-env`

### Cross‑platform (Conda)

If you prefer an isolated environment with Essentia preinstalled:

- Install Miniforge/Mambaforge (recommended)
- Create the environment from `environment.yml`:
  - mamba env create -f environment.yml # or: conda env create -f environment.yml
  - conda activate djlib
  - pip install -e . # install the local package into the conda env

Note: `environment.yml` installs `essentia`, `ffmpeg`, `chromaprint`, and your base Python deps (via pip -r requirements.txt).

## 3) Run audio analysis

- Check env: "TOOLS — Check audio env"
- Analyze and cache features: "STEP 2 — Analyze audio (cache)"
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
