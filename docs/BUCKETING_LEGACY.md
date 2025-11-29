# Bucketing / ML legacy

**Status:** DEPRECATED - This document describes removed/legacy features. Current system (November 2025) uses simple destination folders (library/reject/archive/mixes) with genre classification. Bucketing for smart playlists is a FUTURE enhancement.

Historically the project shipped with a lightweight `SimpleMLBucketAssigner` trained on the public
FMA dataset. The pickled models lived in `models/fma_trained_model*.pkl` and the CLI exposed
commands such as `ml-predict`, `ml-train-local`, `round-1`, and `round-2` that invoked those
helpers.

That pipeline is now retired:

- all FMA models and training scripts were removed from the repository,
- `djlib/bucketing/simple_ml.py` only exposes stubs that raise a clear error if someone
  accidentally imports it,
- CLI commands that previously triggered ML work now print a notice that the legacy flow is gone.

Why? The goal is to build local models powered by Essentia features extracted from your own
library: primarily a **genre classifier** for tag suggestions. The first building block is the CSV export (`ml-export-training-dataset`) that joins Essentia
features and your `genre` labels from the 30 canonical genres in genres.yml. Once we collect at least ~500 labeled tracks we
can implement `train_genre_model` (see `djlib/ml/models.py`).

Playlist/bucket classification is a FUTURE feature - current system uses simple logistics folders (Main Library by artist, Reject, Archive, Mixes).

Until then, treat everything in `djlib/bucketing/` as legacy helpers kept for future reference.
