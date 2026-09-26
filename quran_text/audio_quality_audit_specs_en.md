# Audio Quality Audit Specification

> This document assumes the ayah list has already passed through
> `quran_ayah_filtering_specs.md` (surah openers, short ayat, and excessive
> exact-duplicate repetitions removed). Here we audit **recording quality**
> only, not the text.

## 1. Purpose

Classify every audio file (per reciter, per ayah) into: **accept / reject /
needs manual review**, based on a quality band built from reference samples
already known to be good, rather than fixed thresholds pulled out of thin
air.

## 2. Reciters Shortlist

Only these nine (any reciter outside this list never enters the pipeline at
all):

```
Husary_128kbps
Abdul_Basit_Murattal_192kbps
Abu_Bakr_Ash-Shaatree_128kbps
Minshawy_Murattal_128kbps
Hudhaify_128kbps
Muhammad_Ayyoub_128kbps
Yaser_Salamah_128kbps
aziz_alili_128kbps
Abdullah_Basfar_192kbps
```

## 3. Reference Band Construction

### 3.1 Reference Sample

Ayah **024012** (`024012.mp3`) for each of the nine reciters above → **9
reference files**.

### 3.2 Feature Extraction

For each of the 9 files, extract the same set of features:

| # | Feature | Description | Why |
|---|---|---|---|
| 1 | `rms_dbfs` | Average energy (RMS) in dBFS | Overall loudness level |
| 2 | `peak_dbfs` | Maximum peak level | Detects clipping / too-quiet recordings |
| 3 | `dynamic_range` | `peak_dbfs - rms_dbfs` | Natural recording vs. over-compression |
| 4 | `clipping_ratio` | Ratio of samples near full scale (±1.0) | Digital clipping distortion |
| 5 | `noise_floor_dbfs` | Estimated noise floor (quietest ~10% of frames) | Background noise |
| 6 | `snr_estimate` | Approx. `rms_dbfs - noise_floor_dbfs` | Signal-to-noise ratio |
| 7 | `silence_ratio` | Total silence ratio within the file | Bad trimming / abnormal gaps |
| 8 | `spectral_flatness` | Spectral flatness (0 = tonal, 1 = pure noise) | Noise dominance |
| 9 | `spectral_rolloff` | Frequency below which 85% of energy lies | High-frequency loss (equipment quality) |
| 10 | `sample_rate` | Sample rate | Must match the rest (hard check, not statistical) |
| 11 | `channels` | Number of channels (mono/stereo) | Same as above |

> Note: items 10 and 11 are **not part of the statistical band** — any
> deviation from them = immediate rejection (Hard Rule), because a sample
> rate mismatch isn't "lower quality," it's a structural file error.

### 3.3 Computing the Band from the 9 Samples

For each numeric feature (1–9), from the nine values compute:

```
median_value = median of the nine values
mad_value    = Median Absolute Deviation (MAD) — more robust than std with
               a small n
lower_bound  = median_value - K * mad_value
upper_bound  = median_value + K * mad_value
```

- **Why Median/MAD instead of Mean/Std?** With only 9 samples, a single
  outlier (e.g. one reciter naturally differing slightly on one feature)
  easily distorts the standard deviation. The median is far more stable.
- **`K` (tolerance factor):** suggested starting point `K = 2.5` (a
  relatively wide band, as intended). Adjustable — see Section 9.
- **Minimum band width (Band Floor):** if the nine values are nearly
  identical (`mad_value ≈ 0`), enforce an artificial minimum band width
  (e.g. ± 3 dB for dBFS-based features) to avoid an unrealistically narrow
  band caused purely by chance.

## 4. Hard Rules (Not Subject to the Statistical Band)

These cause immediate rejection regardless of the band, because they
indicate an actual defect rather than a quality trade-off:

- File is corrupted / unreadable / zero duration.
- `sample_rate` or `channels` differs from the value shared by the nine
  reciters.
- `clipping_ratio` above an absolute ceiling (suggested: 0.1%) — regardless
  of the band, since digital clipping is always a defect and never "improves"
  with tolerance.
- `duration` abnormally different from the same ayah number across the other
  reciters (a sign of bad trimming) — suggested threshold: outside
  `median_duration ± 50%` for the same ayah number across the nine.

## 5. Per-File Decision Logic

For every file `(reciter, surah, ayah)` outside the reference set itself:

```python
def audit_file(features: dict, band: dict, hard_checks: dict) -> dict:
    # 1) Hard rules first
    for rule_name, passed in hard_checks.items():
        if not passed:
            return {"decision": "reject", "reason": rule_name}

    # 2) Count how many features fall inside the band
    total = 0
    passed = 0
    borderline_flags = []
    for feat_name, value in features.items():
        if feat_name not in band:
            continue
        lower, upper = band[feat_name]["lower"], band[feat_name]["upper"]
        margin = band[feat_name]["upper"] - band[feat_name]["lower"]
        total += 1
        if lower <= value <= upper:
            passed += 1
        elif (lower - 0.5 * margin) <= value <= (upper + 0.5 * margin):
            borderline_flags.append(feat_name)  # close to the band but outside it

    pass_ratio = passed / total

    # 3) Classification decision (suggested thresholds, adjustable)
    if pass_ratio >= 0.7:
        return {"decision": "accept", "pass_ratio": pass_ratio}
    elif pass_ratio >= 0.5 or borderline_flags:
        return {"decision": "review", "pass_ratio": pass_ratio, "borderline": borderline_flags}
    else:
        return {"decision": "reject", "pass_ratio": pass_ratio}
```

**Why a "pass ratio" instead of requiring every feature to be inside the
band?** Because the band for each feature is built from only 9 samples, so
requiring all features to pass simultaneously compounds strictness (each
feature carries some rejection risk, however small, and these risks stack
up). A ratio (e.g. 7 of 9) preserves the wide, tolerant band you asked for
without making the decision arbitrary.

## 6. The `review` Case

Files classified as `review` are neither auto-accepted nor auto-rejected —
they're collected into a separate list (with the specific borderline
features/reasons) for a quick human listen, since these are the cases most
likely to be genuine edge cases rather than measurement noise.

## 7. Output Schema

```json
{
  "reciter": "Husary_128kbps",
  "surah": 24,
  "ayah": 12,
  "features": { "rms_dbfs": -18.2, "clipping_ratio": 0.0001, "...": "..." },
  "decision": "accept",
  "pass_ratio": 0.89,
  "borderline_features": [],
  "hard_rule_violations": []
}
```

Plus an aggregate summary file: count/ratio of `accept` / `review` /
`reject` per reciter (to catch a reciter being rejected at an unreasonable
rate — a sign the band itself is off, not that the reciter is bad).

## 8. Configurable Parameters

| Parameter | Suggested Starting Value | Description |
|---|---|---|
| `K` | 2.5 | Tolerance factor around the median for the band |
| `accept_threshold` | 0.7 | Pass ratio required for automatic acceptance |
| `review_threshold` | 0.5 | Below this ratio (with no borderline flags) = direct rejection |
| `clipping_hard_limit` | 0.001 (0.1%) | Absolute ceiling for digital clipping |
| `duration_tolerance` | ±50% of the median for the same ayah | Detects bad trimming/merging |
| `band_floor_db` | 3 dB | Minimum band width for dBFS features when MAD ≈ 0 |

These are **starting points only** — see Section 9 for how they get tuned
before being locked in.

## 9. Interactive Calibration Loop

The values in Section 8 (`K`, `accept_threshold`, `review_threshold`,
`clipping_hard_limit`, ...) are a **starting point only**, not final. No
parameter is applied to the full Quran until it has gone through an
interactive calibration loop run by the agent together with the project
owner (human-in-the-loop), as follows:

### 9.1 Loop Mechanics

```
1. The agent runs the pipeline (Sections 3–5) with the default values on a
   small sample only (suggested: 30–50 files), drawn randomly and balanced
   across:
     - the nine reciters (not clustered around a single one)
     - the pass_ratio range: including files near accept_threshold and
       review_threshold (borderline cases are more informative than
       obvious ones)

2. For each file in the sample, the agent presents to the project owner:
     - a link to / playback of the audio file
     - surah and ayah number + reciter name
     - the currently proposed decision (accept / review / reject) + pass_ratio
     - (optional) the specific features that drove the borderline call, if any

3. The project owner listens and gives a simple verdict per sample:
     - "Keep" or "Exclude"
     - no technical justification needed from the project owner — this is a
       purely auditory judgment

4. The agent compares the project owner's verdict against the automatic
   decision:
     - match → no change needed for this case
     - mismatch → this is a calibration signal: the agent records which
       feature(s) drove the automatic decision away from the human judgment

5. The agent proposes an adjustment to one or more parameters (K,
   thresholds, the hard clipping ceiling) based on recurring mismatch
   patterns, and explains the proposed change and its expected effect
   (example: "widening K from 2.5 to 3.0 would have fixed 4 of 5 mismatches
   this round, but would also have accepted one file you manually excluded")

6. The project owner approves / rejects / adjusts the proposed change

7. Steps 1–6 repeat with a fresh sample (no reused files) until reaching a
   "sweet spot": either a full round with zero mismatches between the
   project owner's verdicts and the automatic decisions, or a stable,
   acceptable agreement rate (suggested: ≥ 90% across at least two
   consecutive rounds)

8. Once the sweet spot is reached, the final parameter values are locked in
   and applied once to the entire file set (all nine reciters × all
   text-surviving ayat)
```

### 9.2 Loop Control Rules

- **No repeated samples:** each round pulls files that haven't been shown
  before, to avoid biasing the agent or the project owner toward the same
  examples.
- **Balanced sampling:** each round is roughly balanced across the nine
  reciters, preventing the loop from converging on a parameter that only
  suits a single reciter.
- **Favor borderline cases:** a higher proportion of files near the current
  thresholds (`review` and the `accept`/`reject` boundaries) should be
  drawn, since they're the most useful for tuning `K` — obvious cases
  (clearly excellent or clearly bad) add little new information to the
  calibration.
- **Decision log:** every human verdict + its corresponding automatic
  decision + the parameters in effect at that time is recorded (a table or
  log file), so the full calibration history can be reviewed later, or a
  specific change rolled back.
- **Maximum round cap:** a cap is suggested (e.g. 5–6 rounds) — if the sweet
  spot isn't reached by then, the project owner should be informed that the
  current criteria (the features used in Section 3.2) may not be sufficient
  to separate good from bad, rather than looping indefinitely.

### 9.3 Final Loop Output

Once the loop stops, the agent produces:
- the final approved parameter values (an updated version of the Section 8
  table)
- a complete log of every calibration round (sample counts, agreement
  ratio, adjustments applied, in order)
- only then does it begin applying the full pipeline (Sections 3–7) to the
  entire dataset using the approved parameters.
