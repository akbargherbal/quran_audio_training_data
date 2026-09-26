"""
Feature extraction for the Quran audio-quality filter (makharij training set).

Design goals (see audio_quality_audit_specs_v2_en.md):
  * Content-robust: prefer within-file / relative measures over raw levels.
  * Conservative: measure objective defect signals; do not invent "quality" from
    level differences that are really just reciter style.
  * Cheap enough to run over ~56k files: soundfile decode + a single STFT pass,
    with an ffmpeg fallback so a libsndfile quirk is not mistaken for a defect.

Public API:
    extract(path) -> dict
    extract_many(paths, workers) -> list[dict]
"""

from __future__ import annotations

import math
import os
import subprocess
import numpy as np

try:
    import soundfile as sf
except Exception:  # pragma: no cover
    sf = None

EPS = 1e-10
CLIP_LEVEL = 0.999
FULL_SCALE = 1.0
FRAME_MS = 25.0
HOP_MS = 10.0
SILENCE_DROP_DB = 45.0
NOISE_PCTL = 10.0
FLATNESS_SUBBAND_HZ = 200.0


def _max_run(mask: np.ndarray) -> int:
    if mask.size == 0 or not mask.any():
        return 0
    m = mask.astype(np.int8)
    padded = np.concatenate(([0], m, [0]))
    d = np.diff(padded)
    starts = np.where(d == 1)[0]
    ends = np.where(d == -1)[0]
    return int((ends - starts).max())


def _framed_rms_db(y: np.ndarray, sr: int) -> np.ndarray:
    flen = max(1, int(sr * FRAME_MS / 1000.0))
    hop = max(1, int(sr * HOP_MS / 1000.0))
    if y.size < flen:
        return np.array([], dtype=np.float64)
    n = 1 + (y.size - flen) // hop
    idx = np.arange(flen)[None, :] + hop * np.arange(n)[:, None]
    rms = np.sqrt(np.mean(y[idx].astype(np.float64) ** 2, axis=1))
    return 20.0 * np.log10(np.maximum(rms, EPS))


def _ffprobe_meta(path):
    p = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels",
         "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True, text=True)
    vals = [v for v in p.stdout.strip().replace("\n", ",").split(",") if v]
    sr = int(float(vals[0])) if len(vals) > 0 else 0
    ch = int(float(vals[1])) if len(vals) > 1 else 0
    dur = float(vals[-1]) if len(vals) > 2 else 0.0
    return sr, ch, dur


def _ffmpeg_decode(path):
    """Decode to mono float32 via ffmpeg. Returns (y, sr). Raises on failure."""
    sr, _, _ = _ffprobe_meta(path)
    p = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-ac", "1",
                        "-f", "f32le", "-"], capture_output=True)
    if p.returncode != 0 and not p.stdout:
        raise RuntimeError("ffmpeg_decode_failed")
    y = np.frombuffer(p.stdout, dtype="<f4")
    if y.size == 0:
        raise RuntimeError("ffmpeg_empty")
    return y.astype(np.float32), sr


def extract(path: str) -> dict:
    out: dict = {"path": path, "rel_path": None, "reciter": None,
                 "surah": None, "ayah": None, "error": None,
                 "decode_backend": None}
    base = os.path.basename(path)
    try:
        out["rel_path"] = "/".join(path.replace("\\", "/").split("/")[-3:])
        out["reciter"] = path.replace("\\", "/").rstrip("/").split("/")[-2]
        if len(base) >= 6 and base[:6].isdigit():
            out["surah"] = int(base[0:3])
            out["ayah"] = int(base[3:6])
    except Exception:
        pass

    y = None
    sr = ch = 0
    dur = 0.0
    if sf is not None:
        try:
            info = sf.info(path)
            sr, ch, frames = int(info.samplerate), int(info.channels), int(info.frames)
            dur = frames / sr if sr else 0.0
            y, _ = sf.read(path, dtype="float32", always_2d=False)
            out["decode_backend"] = "soundfile"
        except Exception:
            y = None

    if y is None:
        try:
            y, sr2 = _ffmpeg_decode(path)
            sr = sr or sr2
            ch = ch or 1
            dur = dur or (y.size / sr if sr else 0.0)
            out["decode_backend"] = "ffmpeg"
        except Exception as e:
            out["error"] = "decode_error:%s" % type(e).__name__
            if sr:
                out.update(sample_rate=sr, channels=ch or 1, duration_s=dur)
            return out

    if y.ndim == 2:
        y = y.mean(axis=1)
    y = np.asarray(y, dtype=np.float32)
    if y.size == 0 or dur <= 0:
        out["error"] = "zero_duration"
        return out

    out.update(sample_rate=sr, channels=ch, duration_s=float(dur))
    out["bytes"] = os.path.getsize(path) if os.path.exists(path) else 0
    out["avg_bitrate_kbps"] = round(out["bytes"] * 8.0 / dur / 1000.0, 1) if dur > 0 else 0.0

    peak = float(np.max(np.abs(y)))
    rms = float(np.sqrt(np.mean(y.astype(np.float64) ** 2)))
    out["peak_linear"] = peak
    out["peak_dbfs"] = 20.0 * math.log10(max(peak, EPS))
    out["rms_dbfs"] = 20.0 * math.log10(max(rms, EPS))
    out["dc_offset"] = float(np.mean(y))

    out["clipping_fraction"] = float(np.mean(np.abs(y) >= CLIP_LEVEL))
    out["over_range_fraction"] = float(np.mean(np.abs(y) > FULL_SCALE))
    out["max_clip_run"] = _max_run(np.abs(y) >= CLIP_LEVEL)

    fr = _framed_rms_db(y, sr)
    if fr.size:
        loud = float(np.percentile(fr, 95))
        sil_thr = loud - SILENCE_DROP_DB
        sil_mask = fr < sil_thr
        out["silence_ratio"] = float(np.mean(sil_mask))
        out["noise_floor_dbfs"] = float(np.percentile(fr, NOISE_PCTL))
        active = fr[fr >= sil_thr]
        out["active_rms_dbfs"] = float(np.mean(active)) if active.size else out["rms_dbfs"]
        out["snr_db"] = out["active_rms_dbfs"] - out["noise_floor_dbfs"]
        lead = 0
        while lead < sil_mask.size and sil_mask[lead]:
            lead += 1
        trail = 0
        while trail < sil_mask.size and sil_mask[sil_mask.size - 1 - trail]:
            trail += 1
        hop_s = HOP_MS / 1000.0
        out["leading_silence_s"] = lead * hop_s
        out["trailing_silence_s"] = trail * hop_s
        out["active_duration_s"] = (sil_mask.size - int(sil_mask.sum())) * hop_s
    else:
        out.update(silence_ratio=0.0, noise_floor_dbfs=out["rms_dbfs"],
                   active_rms_dbfs=out["rms_dbfs"], snr_db=0.0,
                   leading_silence_s=0.0, trailing_silence_s=0.0,
                   active_duration_s=dur)

    try:
        import librosa
        n_fft = 2048 if sr >= 16000 else 1024
        S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=max(1, n_fft // 4)))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        keep = freqs >= FLATNESS_SUBBAND_HZ
        S = S[keep, :]
        umean = np.maximum(S.mean(axis=1), EPS)
        out["spectral_flatness"] = float(np.mean(np.exp(np.mean(np.log(umean))) / umean.mean()))
        power = S.sum(axis=1)
        c = np.cumsum(power)
        out["spectral_rolloff"] = float(freqs[keep][int(np.searchsorted(c, 0.85 * c[-1]))]) if c[-1] > 0 else 0.0
        out["spectral_centroid"] = float((freqs[keep][:, None] * S).sum() / max(power.sum(), EPS))
        out["zcr"] = float(np.mean(librosa.feature.zero_crossing_rate(
            y, frame_length=n_fft, hop_length=max(1, n_fft // 4))))
    except Exception as e:
        out["spectral_error"] = type(e).__name__

    return out


def extract_many(paths, workers: int = 8, progress=None):
    from concurrent.futures import ProcessPoolExecutor
    results = [None] * len(paths)
    if workers <= 1:
        for i, p in enumerate(paths):
            results[i] = extract(p)
            if progress:
                progress(i + 1, len(paths))
        return results
    with ProcessPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(extract, paths, chunksize=4)):
            results[i] = r
            if progress:
                progress(i + 1, len(paths))
    return results


FEATURE_COLUMNS = [
    "duration_s", "active_duration_s", "sample_rate", "channels", "avg_bitrate_kbps",
    "rms_dbfs", "peak_dbfs", "peak_linear", "dc_offset",
    "clipping_fraction", "over_range_fraction", "max_clip_run",
    "silence_ratio", "leading_silence_s", "trailing_silence_s",
    "noise_floor_dbfs", "active_rms_dbfs", "snr_db",
    "spectral_flatness", "spectral_rolloff", "spectral_centroid", "zcr",
]
