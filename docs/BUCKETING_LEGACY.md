# Bucketing / ML legacy

Historically the project shipped with a lightweight `SimpleMLBucketAssigner` trained on the public
FMA dataset. The pickled models lived in `models/fma_trained_model*.pkl` and the CLI exposed
commands such as `ml-predict`, `ml-train-local`, `round-1`, and `round-2` that invoked those
helpers.

That pipeline is now retired:

- all FMA models and training scripts were removed from the repository,
- `djlib/bucketing/simple_ml.py` only exposes stubs that raise a clear error if someone
  accidentally imports it,
- CLI commands that previously triggered ML work now print a notice that the legacy flow is gone.

Why? The goal is to build two local models powered by Essentia features extracted from your own
library: a genre classifier for tag suggestions and a bucket classifier for your personal folders.
The first building block is the CSV export (`ml-export-training-dataset`) that joins Essentia
features and your `genre`/`target_subfolder` labels. Once we collect at least ~500 labeled tracks we
can implement `train_genre_model` / `train_bucket_model` (see `djlib/ml/models.py`).

Until then, treat everything in `djlib/bucketing/` as legacy helpers kept for future reference.
