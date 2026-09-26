# Review: `audio_quality_audit_specs_en.md`

Reviewer notes, cross-checked against the actual dataset at `content/Quran_Audio_Data`.

## What I verified in `content/Quran_Audio_Data`

- 9 reciters, exactly **6,236 ayat each** (56,124 main files), all named `SSSAAA.mp3` (6-digit),
  plus an `extras/` folder per reciter (bismillah / `000`-suffixed surah openers, 3-117 files each).
- The spec's reference file `024012.mp3` **exists in all nine** reciters.
- No zero-byte / truncated files in the main set.
- All reference files are 44.1 kHz stereo; the 192 kbps reciters differ in bitrate from the 128 kbps ones.
- **Metadata is not homogeneous:** in a 25-file sample, `Abu_Bakr_Ash-Shaatree_128kbps` contained
  **2 files at 11,025 Hz mono**. The spec's hard rule "sample_rate/channels must equal the value
  shared by the nine" assumes a uniformity this dataset does not have.

## The central problem: the band is built from a single ayah

This is the most serious design flaw, and it is demonstrable. I computed the spec's features on
`024012` for all 9 reciters, then compared with a *single reciter's own* legitimate variation
across 8 different ayat:

| Feature | Cross-reciter band half-width (K=2.5, raw MAD, from `024012`) | Husary's own range across 8 ayat |
|---|---|---|
| `snr_estimate` | +/- 8.8 dB | **15.4 dB** |
| `silence_ratio` | +/- 0.050 | **0.215** |
| `spectral_rolloff` | +/- 807 Hz | 668 Hz |

A reciter's normal, good variation on different ayat is larger than the entire cross-reciter band.
So the band from `024012` will generate a flood of false rejects/reviews on other ayat.

Concretely, from the `024012` band alone, several files **fail their own reference band**:

- `peak_dbfs`: median -2.19, band [-4.52, 0.14] -> Abdul-Basit (-6.5) and Abu-Bakr (-7.2) fall below it.
- `noise_floor` / `snr`: Husary's -54.4 dB floor and 34.8 dB SNR are **outside** the band, i.e. the
  rule penalizes a *cleaner* recording.
- `spectral_flatness`: MAD ~= 0.0003 -> band [0.0017, 0.0032]; 3 of 9 reference files already fall outside.

Two root causes:

1. MAD is unscaled. It should be multiplied by 1.4826 to approximate sigma, so `K = 2.5` is really
   about 1.7 sigma -- far narrower than the spec claims ("relatively wide band").
2. The band floor is only defined for dBFS features, so `spectral_flatness` / `spectral_rolloff`
   get near-zero-width bands whenever eight reciters happen to cluster.

## Other substantive issues

1. **Two-sided bands on directional features.** `noise_floor`, `snr`, `clipping_ratio`, and
   `dynamic_range` only have a *bad direction*. A band rejects "too good," which is wrong. These
   should be one-sided thresholds.

2. **Duration hard rule (+/- 50%) is both too tight and inconsistent.**
   - For `024012`: median = 18.48 s, ceiling = 27.71 s, and Husary's own reference is 27.72 s --
     it fails by 0.009 s.
   - For short ayat it is worse: `001001` median = 5.96 s (ceiling 8.95 s) but Basfar = 11.28 s and
     would be hard-rejected.
   - Reciter tempo, madh pauses, trailing silence, and included bismillah legitimately change duration.
   - Section 4 frames this as "not subject to the statistical band," but `median +/- 50%` *is* a
     statistical rule.
   - Recommendation: silence-trimmed duration with a robust per-ayah z-score (MAD-based, wider), and
     route it to `review`, not hard reject.

3. **Global band conflates reciter identity with quality.** Reciter loudness spans ~8.5 dB, noise
   floor ~24 dB, and `spectral_rolloff` tracks bitrate (128 kbps ~ 2.4-3.7 kHz vs 192 kbps up to
   7.8 kHz). One global band either rejects a whole quiet reciter or is widened until it is
   meaningless. Prefer **per-reciter reference distributions** (needs many ayat, not one) and treat
   128 vs 192 separately for spectral features.

4. **Feature redundancy inflates `pass_ratio`.** `dynamic_range = peak - rms` and
   `snr = rms - noise`, yet `rms`, `peak`, and `noise` are also counted separately. Correlated
   features give loudness/level roughly 5 of 9 votes. Group decorrelated features or weight groups.

5. **`duration` is used by a hard rule but is absent from the feature list (3.2) and the output
   schema (7).** Also missing from output: file path, params/version. Reproducibility gaps: no frame
   size/hop, silence threshold, noise percentile, or clipping threshold defined.

6. **Code-level issues:**
   - `pass_ratio` divides by `total` with no guard for `total == 0`.
   - When `pass_ratio >= accept_threshold`, `borderline_flags` are silently discarded.
   - `borderline_flags` are counted as failures, so the `or borderline_flags` branch cannot rescue a
     ratio below 0.5 (fine, but subtle).

7. **Calibration loop practicalities:**
   - To sample files "near the thresholds" you must first extract features for the whole set (or a
     large pool) and cache them; otherwise borderline sampling is impossible.
   - `review` must be scored against the human's binary Keep/Exclude (define: count only
     accept/reject, or treat review as half).
   - "Favor borderline" needs a random control tail so thresholds do not overfit.
   - A round cap of 5-6 may be hit simply because the band specification is wrong, not because the
     features are insufficient.

8. **Scope gaps:**
   - `extras/` is not mentioned (should be explicitly out of scope, handled upstream).
   - The referenced `quran_ayah_filtering_specs.md` **does not exist in the repo**.

9. **`clipping_ratio` 0.1% absolute:** all 9 reference files clip at 0, so the limit is untested; MP3
   inter-sample overs and decode overshoot can create false positives. Consider true-peak/dBTP plus a
   consecutive-sample run criterion, and validate this ceiling in the loop.

## Recommendations (priority order)

1. **Replace the single reference ayah with a reference corpus**: e.g. 30-100 ayat per reciter
   spanning short/medium/long and different surahs, and build per-reciter feature distributions. If
   a global cross-reciter check is wanted, use it only for gross defects, not fine quality.
2. **Make features content-robust**: compute RMS/SNR/rolloff on silence-trimmed speech only; drop or
   normalize features dominated by content (`spectral_rolloff`, `spectral_flatness`).
3. **One-sided thresholds** for directional features.
4. **Scale MAD** by 1.4826 (or state K in sigma units) and give **every** feature an absolute minimum
   band width, not just dBFS ones.
5. **Fix duration**: silence-trimmed, robust per-ayah comparison, wider tolerance, `review` route.
6. **Add structural-defect features** the current set misses: DC offset, dropout/discontinuity,
   leading/trailing silence length, and a cross-reciter fingerprint to catch wrong-reciter files. Use
   EBU R128 LUFS rather than raw RMS for loudness.
7. **Decide a policy for the 11,025 Hz mono files** -- reject or transcode? Right now the hard rule
   will silently drop them.
8. **Keep the calibration loop** (it is the best part), but add a held-out validation pass after
   locking parameters, and log parameter versions in every output.

## Strengths worth preserving

- Hard-rule vs statistical separation.
- Median + MAD instead of mean + std.
- Human-in-the-loop calibration with decision logging and a round cap.
- Per-reciter aggregate to catch a whole reciter being rejected.
- The "insufficient features" escape hatch.

## Suggested next steps

- (a) Rewrite the spec incorporating the changes above, or
- (b) Prototype the feature extractor to empirically test the alternative band design.

Note: a full metadata scan of all 56k files was running in the background; exact
sample-rate/channel/bitrate counts can be appended when it completes.
