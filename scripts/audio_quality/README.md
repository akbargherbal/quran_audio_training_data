# audio_quality pipeline

Conservative audio-quality filter for the Quran makharij training set.
See `quran_text/audio_quality_audit_specs_v2_en.md` for the full specification
and `quran_text/audio_quality_audit_review.md` for why v1 was replaced.

## Files

- `features.py` — per-file feature extraction (soundfile primary, ffmpeg fallback).
- `audit.py` — `extract` and `decide` CLI.
- `make_sample.py` — builds a balanced (ayah x 9 reciters) sample.

## Usage

```bash
# build a sample: <n_random_ayat> <seed>
python3 scripts/audio_quality/make_sample.py 45 7

# extract features
python3 scripts/audio_quality/audit.py extract \
    --files reports/sample_files.txt --out reports/sample1_features.csv --workers 8

# apply conservative rules (per-reciter baselines from the same file by default)
python3 scripts/audio_quality/audit.py decide \
    --features reports/sample1_features.csv --outdir reports/sample1

# held-out use: build baselines on a reference set, apply to new files
python3 scripts/audio_quality/audit.py decide \
    --features reports/sample2_features.csv \
    --baseline-features reports/sample1_features.csv \
    --outdir reports/sample2
```

Outputs per run: `decisions.jsonl`, `decisions.csv`, `baselines.json`, `summary.md`.

## Full-dataset run

```bash
cd /content/Quran_Audio_Data
find . -mindepth 2 -maxdepth 2 -name '*.mp3' ! -path '*/extras/*' -print0 \
  | tr '\0' '\n' | sed 's#^\./#/content/Quran_Audio_Data/#' > /tmp/all_files.txt
python3 /content/quran_audio_filtering/scripts/audio_quality/audit.py extract \
  --files /tmp/all_files.txt --out /tmp/all_features.csv --workers 16
```

Extraction is the expensive step (~7 files/s at 8 workers here). Features are
cacheable: re-running `decide` is instant. Tune only `CONFIG` in `audit.py`,
and validate changes with the calibration loop in the spec.
