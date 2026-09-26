# Audio Quality Audit Specification v2 (for Makharij Training Data)

> Supersedes `audio_quality_audit_specs_en.md`. Read alongside
> `audio_quality_audit_review.md` (why v1 was changed) and
> `quran_ayah_filtering_specs.md` (text-level filtering that runs first).

## 1. Purpose

Filter the per-ayah recordings of the nine reciters so that what enters the
training set is free of **objective audio defects**, while aggressively avoiding
**false positives** (dropping good recordings). The downstream model learns
correct Arabic articulation points (makharij); "garbage in, garbage out" means a
few clearly bad files must go, but a conservative posture matters more than
maximizing the reject rate.

The output is a three-way decision per file: **accept / review / reject**.
* `accept` enters the dataset.
* `review` is a short human-listen list.
* `reject` is dropped.

## 2. Design Principles

1. **Reject only unambiguous defects.** Corruption, no speech, sub-16 kHz
   fidelity, decoded over-range garbage, buried-in-noise, severe clipping.
2. **Judge quality relative to the same reciter, not globally.** Reciters differ
   systematically in loudness, noise floor and recording gear; a global band
   rejects whole reciters. v1 did exactly this.
3. **Judge duration relative to the same ayah across reciters.** Identical text
   makes cross-reciter duration comparison fair; tempo differences still get a
   wide tolerance and go to `review`, never `reject`.
4. **One-sided where directional.** "Cleaner than reference" must never flag.
5. **No single reference ayah.** The per-reciter baseline is built from many
   ayat (see 5).
6. **`review` is the default for uncertainty**, never silent rejection.

## 3. Dataset Reality (measured)

`content/Quran_Audio_Data`, 9 reciters x 6,236 files = 56,124 main files, plus
per-reciter `extras/` (bismillah / `000` surah openers) which are **out of scope**
(handled by the upstream text filter).

Structural findings from a full metadata scan:

| Reciter | Files | Anomaly |
|---|---|---|
| Abu_Bakr_Ash-Shaatree_128kbps | 6,236 | ~298 files at 11,025 Hz mono, ~24 kbps |
| Yaser_Salamah_128kbps | 6,236 | ~165 files at 48,000 Hz stereo (fine), 1 at 22,050 Hz |
| Hudhaify_128kbps | 6,236 | 1 file at 22,050 Hz mono, 24 kbps |
| all others | 6,236 each | 44,100 Hz stereo |

Plus at least 5 genuinely corrupt files found in a 405-file sample
(decoder errors / over-range samples), concentrated in `aziz_alili` and
`Abdul_Basit`.

**Decision (owner-approved):** hard-exclude only low-fidelity files
(`sample_rate < 16000`); route reduced but usable rates (16-44.1 kHz) to `review`.

## 4. Hard Rules (immediate `reject`)

These indicate a defect, not a quality trade-off:

- Unreadable/corrupt (decode fails via both soundfile and ffmpeg).
- `sample_rate` below `min_sample_rate` (16,000 Hz).
- `active_duration_s` below `min_active_duration_s` (0.5 s) or `active_rms_dbfs`
  below `min_active_rms_dbfs` (-50 dBFS): effectively no speech.
- Over-range decode: `peak_linear > 2.0` **or** `over_range_fraction > 0.005`
  (samples beyond full scale) -- corrupt frames / decoder garbage.
- Severe clipping: `clipping_fraction >= 0.01` **or** `max_clip_run >= 100`.
- `snr_db < 3.0` (signal buried in noise).

## 5. Per-Reciter Baselines (replaces v1's global band)

A **reference corpus** is built per reciter from a large, representative set of
that reciter's ayat (all 6,236, or a fixed random subset). For each feature in
{`snr_db`, `spectral_flatness`, `silence_ratio`, `clipping_fraction`,
`dc_offset`, `rms_dbfs`} store the **median** (robust to outliers). Baselines are
saved to `baselines.json` and reused; they are never re-derived from the file
being judged.

Baselines are applied as **ratios + absolute floors**, not as a pass-count band:

| Check | Review condition | Rationale |
|---|---|---|
| SN(R) | `snr_db < 0.5 * reciter_median(snr_db)` **and** `< 10 dB` | unusually noisy *for this reciter*, and low in absolute terms |
| Noise dominance | `spectral_flatness > 4 * reciter_median` **and** `> 0.10` | only genuinely hissy files; content/residual prevents near-zero-baseline false positives |
| Silence | `silence_ratio > 3 * reciter_median` **and** `> 0.50` | bad trimming / big internal gaps |
| Leading/trailing silence | `> 3.0 s` | over-long edges |
| DC offset | `|dc_offset| > 0.05` | recording bias |
| Clipping | `clipping_fraction >= 0.001` **or** `max_clip_run >= 20` | mild clipping |
| Bitrate | `avg_bitrate_kbps < 32` | low-fidelity encode |
| Sample rate | `sample_rate < 44,100` (but `>= 16,000`) | reduced fidelity |

All of these produce **`review`**, never `reject`.

## 6. Cross-Ayah Duration Check

For each ayah with at least `cross_min_group = 5` reciters present, compute the
median `duration_s`. A file is flagged `review` when

```
duration_s / median_duration_s  outside  [0.50, 2.00]
```

This catches truncation/merging/bad trimming without punishing normal tempo.
(The v1 `+/-50%` rule rejected Husary's own reference file; this rule does not.)

## 7. Decision Logic

```
hard, review = [], []            # from sections 4, 5, 6
if hard:                 -> reject
elif review:             -> review
else:                    -> accept
```

Hard reasons take precedence; review reasons are still recorded for transparency.

## 8. Configurable Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `min_sample_rate` | 16000 | below -> reject |
| `target_sample_rate` | 44100 | below (and >= min) -> review |
| `min_bitrate_kbps` | 32 | below -> review |
| `min_active_duration_s` | 0.5 | below -> reject |
| `min_active_rms_dbfs` | -50 | below -> reject |
| `over_range_reject` | 0.005 | fraction of samples beyond full scale -> reject |
| `peak_linear_reject` | 2.0 | decoded peak -> reject |
| `clip_fraction_reject` | 0.010 | -> reject |
| `clip_run_reject` | 100 | -> reject |
| `snr_reject_db` | 3.0 | -> reject |
| `clip_fraction_review` | 0.001 | -> review |
| `clip_run_review` | 20 | -> review |
| `dc_offset_review` | 0.05 | -> review |
| `snr_review_ratio` | 0.50 | of reciter median -> review |
| `snr_review_abs_db` | 10.0 | and below this -> review |
| `flatness_review_factor` / `_abs` | 4.0 / 0.10 | -> review |
| `silence_review_factor` / `_abs` | 3.0 / 0.50 | -> review |
| `edge_silence_review_s` | 3.0 | -> review |
| `cross_min_group` | 5 | min reciters to compare duration |
| `duration_ratio_review` | (0.50, 2.00) | -> review |

## 9. Feature Set

Computed per file (implementation: `scripts/audio_quality/features.py`):

`duration_s`, `active_duration_s`, `sample_rate`, `channels`, `avg_bitrate_kbps`,
`rms_dbfs`, `peak_dbfs`, `peak_linear`, `dc_offset`, `clipping_fraction`,
`over_range_fraction`, `max_clip_run`, `silence_ratio`,
`leading_silence_s`, `trailing_silence_s`, `noise_floor_dbfs`,
`active_rms_dbfs`, `snr_db`, `spectral_flatness`, `spectral_rolloff`,
`spectral_centroid`, `zcr`.

Decoding: `soundfile` primary, `ffmpeg` fallback (so a libsndfile quirk is not
recorded as a defect). `snr_db` uses loud-frame energy vs the 10th-percentile
frame noise floor, and `silence_ratio` is relative to the file's loud frames, so
reciter loudness does not distort them. `spectral_rolloff` is deliberately **not**
used for decisions (it tracks bitrate/content, not quality).

## 10. Output

Per file (`decisions.jsonl` / `decisions.csv`):

```json
{
  "reciter": "Husary_128kbps",
  "surah": 24, "ayah": 12,
  "decision": "accept",
  "hard": [],
  "review": [],
  "path": ".../Husary_128kbps/024012.mp3"
}
```

Plus `baselines.json` (per-reciter medians) and `summary.md` (decision counts
overall and per reciter, and reason frequencies).

## 11. Calibration Loop (human-in-the-loop)

1. Extract features for a balanced sample (all 9 reciters, including known
   anomalies). Build baselines on a **reference** set.
2. Apply the rules; collect the `review` list (and any `reject` the owner wants
   to double-check).
3. Owner listens and marks Keep / Exclude (binary), no technical justification.
4. Compare with automatic decisions; log every verdict + parameters.
5. For recurring mismatches, adjust **one** parameter and show the expected
   effect (how many mismatches it fixes, how many good files it would newly
   exclude).
6. Repeat on **fresh, disjoint** samples, keeping baseline sets fixed, until
   >= 90% agreement over two consecutive rounds or a round with zero mismatches.
7. Lock parameters, then apply to the full 56,124 less text-filtered ayat.

Because the pipeline defaults to `review` (not `reject`) under uncertainty, a
mismatch is cheap: worst case a good file waits for a listen instead of being
dropped.

## 12. Measured Prototype Results

Prototype: `scripts/audio_quality/` on two independent 405-file samples
(45 random ayat + known-anomaly ayat, x 9 reciters). Sample 2 used baselines
built only on sample 1 (held-out test).

| Run | accept | review | reject | notes |
|---|---|---|---|---|
| Sample 1 (45 ayat) | 386 (95.3%) | 10 (2.5%) | 9 (2.2%) | rejects = 4 x 11 kHz Abu Bakr + 5 corrupt aziz |
| Sample 2 (held-out) | 388 (95.8%) | 11 (2.7%) | 6 (1.5%) | rejects = 6 x 11 kHz Abu Bakr |

Good files are kept, the known-corrupt and low-fidelity files are caught, and a
tiny `review` list remains for the owner. This is the starting point for the
Section 11 loop.

## 13. Running It

```bash
# 1. build a sample (or use the full recursive listing)
python3 scripts/audio_quality/make_sample.py 45 7

# 2. extract features (soundfile primary, ffmpeg fallback)
python3 scripts/audio_quality/audit.py extract \
    --files reports/sample_files.txt --out reports/sample_features.csv --workers 8

# 3. decide (uses per-reciter baselines; optionally from a reference set)
python3 scripts/audio_quality/audit.py decide \
    --features reports/sample_features.csv --outdir reports/sample1
```

For the full run, list all main `.mp3` files (excluding `extras/`), extract once
(features are cacheable), build baselines, then apply. Full extraction is the
expensive step (~7 files/s at 8 workers in this environment).

Audio is never committed to git. Assemble the filtered tree and upload it to the
bucket at the end:

```bash
python3 scripts/audio_quality/assemble_and_upload.py \
    --decisions reports/all/decisions.jsonl --include accepted,review \
    --reports reports/all --stage /content/quran_filtered_stage --upload
# -> gs://sheikh-fitzgerald-backup/ARABIC_DATA/Quran_Filtered_Audio_Data/
```

## 14. Out of Scope

- Text-level filtering (surah openers, <5-word ayat, exact-duplicate bias).
- `extras/` (bismillah and `000` surah opener files).
- Loudness normalization for training (recommended as a separate, reversible
  preprocessing step; **not** a quality criterion here).
- Audio storage. Filtered audio is uploaded to
  `gs://sheikh-fitzgerald-backup/ARABIC_DATA/Quran_Filtered_Audio_Data/`
  and is **never** committed to GitHub.
