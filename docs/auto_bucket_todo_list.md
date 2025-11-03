# Auto-Bucket Module TODO List

## v0.1 – MVP (Simple ML Bucket Assigner)

### Core Implementation

- [ ] Create `SimpleMLBucketAssigner` class in `djlib/bucketing/simple_ml.py`
- [ ] Implement feature extraction: BPM, key, genre tokens from track data
- [ ] Create CSV parser for input training data with bucket labels
- [ ] Implement model training using RandomForestClassifier from scikit-learn
- [ ] Create `predict()` function for single track prediction
- [ ] Create `predict_batch()` function for multiple tracks
- [ ] Implement CSV export functionality for predictions (`bucket_predictions.csv`)

### Testing & Validation

- [ ] Write unit tests in `tests/test_simple_ml.py`
- [ ] Implement metrics calculation (accuracy, f1-score, confusion matrix)
- [ ] Create `metrics.json` output format for model evaluation
- [ ] Add model validation and cross-validation support

### Documentation & Integration

- [ ] Create README with implementation details and results
- [ ] Add CLI integration script `assign_buckets.py`
- [ ] Implement basic error handling and edge cases
- [ ] Add logging for predictions and model performance

## v0.2 – Feedback Loop (Personalization)

### Feedback Processing

- [ ] Create feedback CSV parser (`track_id, correct_bucket` format)
- [ ] Implement feedback data validation and cleaning
- [ ] Add feedback storage and management system

### Model Updates

- [ ] Implement incremental learning from user feedback
- [ ] Create retraining pipeline that updates existing model
- [ ] Add feedback quality assessment (confidence scoring)

### Evaluation & Monitoring

- [ ] Implement before/after accuracy comparison
- [ ] Create feedback analysis reports
- [ ] Add model version tracking for feedback-based updates
- [ ] Implement gradual model improvement metrics

## v0.3 – Hybrid Model with SBERT Embeddings

### SBERT Integration

- [ ] Implement SBERT embedding generation using `all-MiniLM-L6-v2`
- [ ] Create text preprocessing for embedding input: `"{artist} - {title}, {genres}, BPM {bpm}, key {key}"`
- [ ] Add embedding caching for performance optimization

### Hybrid Architecture

- [ ] Implement feature concatenation (SBERT embeddings + numerical features)
- [ ] Create new classifier options (MLP, XGBoost, enhanced RandomForest)
- [ ] Add dimensionality reduction if needed (PCA/UMAP)

### Comparison & Validation

- [ ] Implement side-by-side model comparison framework
- [ ] Create automated benchmarking against v0.1 baseline
- [ ] Add statistical significance testing for improvements
- [ ] Generate detailed comparison reports

## Infrastructure & DevOps

### Model Persistence

- [ ] Implement model serialization to `models/bucket_model.pkl`
- [ ] Add model versioning with hash/timestamp
- [ ] Create model backup and rollback capabilities
- [ ] Implement model metadata storage (training date, version, metrics)

### CLI Integration

- [ ] Create `assign_buckets.py` CLI script for DJ Library Manager integration
- [ ] Implement `--feedback` flag for retraining from user corrections
- [ ] Add `--debug` mode for detailed prediction explanations
- [ ] Create batch processing capabilities for large datasets

### Edge Cases & Robustness

- [ ] Handle missing BPM/key data (assign to 'unsure' bucket)
- [ ] Implement low confidence prediction handling (< 0.5 probability)
- [ ] Add logging to `logs/low_confidence.csv` for edge cases
- [ ] Create fallback mechanisms for prediction failures

## UX & User Experience

### User Interaction

- [ ] Implement user-friendly CSV editing workflow
- [ ] Create clear feedback submission process
- [ ] Add prediction confidence visualization
- [ ] Implement progressive disclosure of advanced options

### Evaluation Metrics

- [ ] Track user acceptance rate (target: >= 80% correct predictions)
- [ ] Implement automated feedback analysis reports
- [ ] Create user satisfaction metrics
- [ ] Add A/B testing capabilities for model improvements

### Debug & Transparency

- [ ] Implement `--debug` flag with detailed prediction explanations
- [ ] Add feature importance visualization
- [ ] Create prediction audit trails
- [ ] Implement model interpretability features

## Dependencies & Environment

### Core Dependencies (v0.1)

- [ ] Install scikit-learn==1.3.0
- [ ] Install pandas>=1.5
- [ ] Install numpy>=1.24
- [ ] Add dependency management to requirements.txt

### Extended Dependencies (v0.2+)

- [ ] Install sentence-transformers==2.2.2 for SBERT
- [ ] Install xgboost>=1.7 for advanced classification
- [ ] Add optional dependency handling

### Development Setup

- [ ] Create development environment configuration
- [ ] Add testing data generation scripts
- [ ] Implement CI/CD pipeline for model validation
- [ ] Create documentation for setup and usage

## Data Management

### Training Data

- [ ] Create `training.csv` format validation
- [ ] Implement data preprocessing pipeline
- [ ] Add data quality checks and cleaning
- [ ] Create synthetic data generation for testing

### Prediction Outputs

- [ ] Standardize `bucket_predictions.csv` format
- [ ] Implement prediction confidence scoring
- [ ] Add prediction metadata (timestamp, model version)
- [ ] Create prediction result validation

## Future Enhancements (Post-v0.3)

### Advanced Features

- [ ] Implement online learning capabilities
- [ ] Add multi-label classification for complex buckets
- [ ] Create ensemble model approaches
- [ ] Implement temporal trend analysis

### Performance Optimization

- [ ] Add GPU acceleration support
- [ ] Implement model quantization for edge deployment
- [ ] Create distributed training capabilities
- [ ] Add real-time prediction optimization

### Integration Features

- [ ] Create REST API for external integrations
- [ ] Implement webhook notifications for predictions
- [ ] Add database integration options
- [ ] Create plugin architecture for custom features</content>
      <parameter name="filePath">/Users/sztuka/Projects/dj-library-manager/docs/auto_bucket_todo_list.md
