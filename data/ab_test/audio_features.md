# Audio Features Reference — Essentia → Semantic Descriptors

This file serves a dual purpose:

1. **Human documentation** — explains what each Essentia feature means and its typical ranges
2. **LLM reference sheet** — injected into the interpreter prompt as `{audio_features_reference}`

The interpreter converts ~20 raw Essentia features into 6 semantic descriptors:
**Tempo, Rhythm, Energy, Timbre, Texture, Harmony**

---

## Feature Inventory

### Available features (from `djlib/audio/essentia_backend.py`)

Our Essentia MusicExtractor pipeline produces ~50+ features per track.
The interpreter receives a **curated subset of ~20 features** grouped by descriptor.

#### Feature name mapping

Some feature names in the interpreter input differ from academic names:

| Interpreter input key      | Essentia source                                       | Notes                                                 |
| -------------------------- | ----------------------------------------------------- | ----------------------------------------------------- |
| `energy`                   | `Energy` algorithm (lowlevel)                         | Sum of squared signal values, NOT perceptual loudness |
| `bpm_conf`                 | `RhythmExtractor2013` confidence                      | 0–1, how reliable the BPM estimate is                 |
| `tuning_diatonic_strength` | `Key` algorithm with diatonic profile on 120-bin HPCP | 0–1, tonality strength                                |
| `spec_centroid`            | `Centroid` of spectrum                                | Hz, center of spectral mass                           |
| `spec_rolloff`             | `RollOff` at 85% energy                               | Hz, frequency below which 85% energy                  |
| `onset_rate`               | `OnsetRate`                                           | Onsets per second                                     |
| `danceability`             | `Danceability` algorithm                              | Based on DFA of rhythm (0–~3)                         |
| `dyn_complex`              | `DynamicComplexity`                                   | Avg absolute deviation from global loudness (0–~15)   |
| `lufs`                     | `LoudnessEBUR128` integrated                          | dB LUFS, perceptual loudness                          |
| `spec_flux_mean`           | `Flux` mean across frames                             | Spectral change rate                                  |
| `spec_flatness_mean`       | `FlatnessDB` mean                                     | 0=tonal, 1=noise-like                                 |
| `hfc_mean`                 | `HFC` (High Frequency Content) mean                   | Amount of high-frequency energy                       |
| `mfcc_0..4`                | `MFCC` coefficients 0–4                               | Spectral envelope shape                               |
| `chords_changes_rate`      | `ChordsDescriptors`                                   | 0–1, ratio of chord changes to total chords           |
| `zero_crossing_rate`       | `ZeroCrossingRate`                                    | 0–1, noisiness indicator                              |

#### Features NOT sent to interpreter (intentionally excluded)

- `mfcc_5..12` — diminishing returns, too abstract for LLM
- `chroma_0..11` raw bins — 12 values of noise for LLM
- `chroma_std_0..11` — same reason
- `tonnetz_mean/std` — too abstract, minimal discrimination power
- `spec_bandwidth_*` — redundant with centroid + rolloff
- `spec_contrast_*` — redundant with flatness + centroid std
- Individual `mfcc_kurtosis_*`, `mfcc_skew_*` per coefficient — aggregates only

---

## Semantic Ranges by Descriptor

### 1. Tempo

**Input features:** `onset_rate`, BPM (from rekordbox, passed separately)

| Feature      | Range            | Interpretation                                  |
| ------------ | ---------------- | ----------------------------------------------- |
| `onset_rate` | 0–15+ onsets/sec | Rhythmic density / event rate                   |
|              | < 2              | Very sparse — ambient pads, sustained tones     |
|              | 2–4              | Sparse — minimal beats, slow rhythms            |
|              | 4–7              | Moderate — standard pop/rock/house              |
|              | 7–10             | Dense — fast percussion, double-time patterns   |
|              | > 10             | Very dense — drum fills, breakcore, complex DnB |

> **Note:** onset_rate ≠ BPM. A track at 70 BPM can have onset_rate 8 (complex percussion). A track at 140 BPM can have onset_rate 3 (minimal kick pattern).

### 2. Rhythm

**Input features:** `danceability`, `onset_rate`, `dyn_complex`, `bpm_conf`

| Feature        | Range   | Interpretation                                     |
| -------------- | ------- | -------------------------------------------------- |
| `danceability` | 0–~3    | Higher = more regular, danceable pulse             |
|                | < 0.5   | Not danceable — freeform, ambient, spoken word     |
|                | 0.5–1.0 | Low danceability — jazz, experimental, ballads     |
|                | 1.0–1.5 | Moderately danceable — pop, rock, hip-hop          |
|                | 1.5–2.0 | Danceable — house, disco, techno                   |
|                | > 2.0   | Very danceable — strong regular 4/4 groove         |
| `dyn_complex`  | 0–~15   | Loudness variation over time                       |
|                | < 2     | Flat, compressed (heavily mastered club tracks)    |
|                | 2–4     | Moderate dynamics (standard production)            |
|                | 4–7     | Dynamic (live recordings, acoustic, jazz)          |
|                | > 7     | Very dynamic (classical, unmastered, live improv)  |
| `bpm_conf`     | 0–1     | BPM estimation reliability                         |
|                | < 0.3   | Unreliable — freeform tempo, rubato, no clear beat |
|                | 0.3–0.6 | Moderate — tempo changes or ambiguous meter        |
|                | > 0.6   | High confidence — stable, clear tempo              |

### 3. Energy

**Input features:** `energy`, `lufs`, `spec_flux_mean`

| Feature          | Range       | Interpretation                                         |
| ---------------- | ----------- | ------------------------------------------------------ |
| `energy`         | 0–~1+       | Signal energy (sum of squares, normalized to duration) |
|                  | < 0.05      | Very low — quiet, sparse, ambient                      |
|                  | 0.05–0.2    | Low — soft acoustic, chill                             |
|                  | 0.2–0.5     | Moderate — standard mix levels                         |
|                  | 0.5–0.8     | High — loud, driving                                   |
|                  | > 0.8       | Very high — wall of sound, heavily compressed          |
| `lufs`           | -40 to 0 dB | Perceived loudness (EBU R128)                          |
|                  | < -20       | Quiet — ambient, field recordings                      |
|                  | -20 to -14  | Moderate — standard dynamic range                      |
|                  | -14 to -8   | Loud — modern mastered music                           |
|                  | > -8        | Very loud — loudness war, heavily compressed           |
| `spec_flux_mean` | 0–~1+       | Rate of spectral change between frames                 |
|                  | < 0.05      | Very stable — drones, sustained tones                  |
|                  | 0.05–0.15   | Moderate change — typical music                        |
|                  | 0.15–0.3    | Active — many transients, percussive                   |
|                  | > 0.3       | Very active — chaotic, dense percussion                |

### 4. Timbre

**Input features:** `spec_centroid`, `spec_centroid_std`, `spec_rolloff`, `spec_rolloff_std`, `hfc_mean`, `mfcc_0..4`, `mfcc_kurtosis_mean`, `mfcc_skew_mean`

| Feature              | Range        | Interpretation                                              |
| -------------------- | ------------ | ----------------------------------------------------------- |
| `spec_centroid`      | 0–11025 Hz   | Spectral "center of mass" — perceived brightness            |
|                      | < 1000       | Very dark — sub-bass, very warm pads                        |
|                      | 1000–1500    | Dark/warm — dub, deep house, trip hop                       |
|                      | 1500–2500    | Balanced — most pop, rock, mid-range focused                |
|                      | 2500–4000    | Bright — electronic, synth-heavy, hi-hats                   |
|                      | > 4000       | Very bright — harsh, crispy treble, noise                   |
| `spec_centroid_std`  | 0–3000+      | Timbral variation over time                                 |
|                      | < 300        | Very consistent timbre (drone, pad)                         |
|                      | 300–800      | Moderate variation (typical song structure)                 |
|                      | > 800        | High variation (many instrument changes, builds)            |
| `spec_rolloff`       | 0–22050 Hz   | Frequency below which 85% of energy sits                    |
|                      | < 2000       | Most energy in bass/low-mids                                |
|                      | 2000–5000    | Balanced frequency range                                    |
|                      | 5000–10000   | High-frequency content present                              |
|                      | > 10000      | Lots of treble content (cymbals, hi-hats, air)              |
| `hfc_mean`           | 0–~200+      | High-frequency energy concentration                         |
|                      | < 10         | Minimal highs — deep, subby                                 |
|                      | 10–50        | Moderate — standard mix                                     |
|                      | 50–100       | Notable high content — electronic, metallic                 |
|                      | > 100        | Very heavy highs — distortion, aggressive                   |
| `mfcc_0`             | -900 to -200 | Overall spectral energy level                               |
| `mfcc_1`             | -50 to 200   | Spectral tilt: positive = more bass, negative = more treble |
| `mfcc_2`             | -50 to 80    | Even vs odd harmonics balance                               |
| `mfcc_3`             | -40 to 50    | Mid-frequency detail                                        |
| `mfcc_4`             | -30 to 30    | Fine spectral structure                                     |
| `mfcc_kurtosis_mean` | -2 to 10+    | Spectral shape peakedness (-=flat, +=peaked)                |
| `mfcc_skew_mean`     | -3 to 3      | Spectral asymmetry                                          |

### 5. Texture

**Input features:** `spec_flatness_mean`, `zero_crossing_rate`, `spec_centroid_std`, `dyn_complex`

| Feature              | Range     | Interpretation                                       |
| -------------------- | --------- | ---------------------------------------------------- |
| `spec_flatness_mean` | 0–1       | Noise-like vs tonal character                        |
|                      | < 0.05    | Very tonal — clear pitched instruments, synths       |
|                      | 0.05–0.15 | Mostly tonal with some noise — standard mix          |
|                      | 0.15–0.3  | Mixed — noisy percussion + tonal elements            |
|                      | > 0.3     | Noise-dominant — distortion, heavy percussion, noise |
| `zero_crossing_rate` | 0–1       | Signal sign-change rate                              |
|                      | < 0.03    | Very smooth — bass-heavy, clean tones                |
|                      | 0.03–0.07 | Normal — mixed frequency content                     |
|                      | 0.07–0.12 | Noisy — lots of high-frequency transients            |
|                      | > 0.12    | Very noisy — distorted, harsh                        |

> **Texture** also uses `spec_centroid_std` (timbral consistency) and `dyn_complex` (dynamic range) from above tables.

### 6. Harmony

**Input features:** `chords_changes_rate`, `tuning_diatonic_strength`

| Feature                    | Range     | Interpretation                                            |
| -------------------------- | --------- | --------------------------------------------------------- |
| `chords_changes_rate`      | 0–1       | How often chords change relative to total chords detected |
|                            | < 0.05    | Very static — single chord or drone, repetitive           |
|                            | 0.05–0.15 | Low change — groove-based, loop-driven                    |
|                            | 0.15–0.35 | Moderate — typical verse-chorus structure                 |
|                            | > 0.35    | High change — jazz, prog, complex harmony                 |
| `tuning_diatonic_strength` | 0–1       | How strongly the audio fits a diatonic (Western) scale    |
|                            | < 0.3     | Weak — atonal, percussive, non-Western                    |
|                            | 0.3–0.5   | Moderate — some tonal content but not dominant            |
|                            | 0.5–0.7   | Tonal — clear key, melodic content                        |
|                            | > 0.7     | Strongly tonal — very melodic, clear key                  |

---

## Cross-feature Patterns (for interpreter context)

These combinations are more informative than individual features:

| Pattern                             | Features                      | Indicates                                               |
| ----------------------------------- | ----------------------------- | ------------------------------------------------------- |
| High energy + low centroid          | energy > 0.5, centroid < 1500 | Heavy bass-driven music (dub, bass music, hip-hop)      |
| High energy + high centroid         | energy > 0.5, centroid > 3000 | Aggressive electronic (hard techno, trance, DnB)        |
| High danceability + low dyn_complex | dance > 1.5, dyn < 2.5        | Club-optimized production (house, techno)               |
| Low danceability + high dyn_complex | dance < 0.8, dyn > 5          | Live/acoustic performance (jazz, folk, classical)       |
| High onset_rate + high danceability | onset > 7, dance > 1.5        | Complex rhythm with groove (funk, afrobeats, DnB)       |
| High hfc + low centroid             | hfc > 50, centroid < 2000     | Hi-hats/cymbals over dark base (deep house, dub techno) |
| Low chords_rate + low diatonic      | chords < 0.05, diatonic < 0.3 | Percussive/atonal (techno, drum loops, noise)           |
| High chords_rate + high diatonic    | chords > 0.2, diatonic > 0.6  | Harmonically rich melodic music (pop, jazz, soul)       |
