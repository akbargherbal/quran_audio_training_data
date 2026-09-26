"""
Conservative quality auditor for the Quran makharij training set.

Philosophy (see audio_quality_audit_specs_v2_en.md):
  * HARD REJECT only for unambiguous defects: unreadable/corrupt, no speech,
    sub-16 kHz fidelity, over-range (decoder/clipping garbage), buried in noise,
    severe digital clipping. These are rare.
  * REVIEW (human listen) for anything borderline. Never auto-reject on a soft
    statistical deviation -- false positives (dropping good data) are the
    expensive error for a training set.
  * Quality is judged *relative to the same reciter* (per-reciter median), and
    duration is judged relative to the *same ayah* across reciters. This removes
    the reciter/tempo confounds that made a single global band reject whole
    reciters.

CLI:
    python audit.py extract --files <list.txt> --out features.csv [--workers 8]
    python audit.py decide  --features features.csv --outdir reports/
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

CONFIG = {
    # --- fidelity (structural) ---
    "min_sample_rate": 16000,        # < -> reject (e.g. 11025 Hz)
    "target_sample_rate": 44100,     # < this (but >= min) -> review (e.g. 22050)
    "min_bitrate_kbps": 32.0,        # < -> review (e.g. 24 kbps)

    # --- corruption / speech presence (hard) ---
    "min_active_duration_s": 0.5,
    "min_active_rms_dbfs": -50.0,
    "over_range_reject": 0.005,      # >0.5% of samples beyond full scale
    "peak_linear_reject": 2.0,       # decoded peak far above 1.0
    "clip_fraction_reject": 0.010,   # >1% of samples hard-clipped
    "clip_run_reject": 100,
    "snr_reject_db": 3.0,

    # --- soft absolute defects (review) ---
    "clip_fraction_review": 0.001,
    "clip_run_review": 20,
    "dc_offset_review": 0.05,
    "snr_review_ratio": 0.50,        # below half the reciter's own median ...
    "snr_review_abs_db": 10.0,       # ... and below this absolute value
    "flatness_review_factor": 4.0,   # > 4x the reciter's own median flatness ...
    "flatness_review_abs": 0.10,     # ... and genuinely hissy in absolute terms
    "silence_review_factor": 3.0,    # > 3x the reciter's own median silence ...
    "silence_review_abs": 0.50,      # ... and above this absolute value
    "edge_silence_review_s": 3.0,

    # --- cross-ayah duration (review only) ---
    "cross_min_group": 5,
    "duration_ratio_review": (0.50, 2.00),
}


def load_features(path):
    rows = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for k, v in list(r.items()):
                if k in ("path", "rel_path", "reciter", "error", "spectral_error", "decode_backend"):
                    continue
                if v == "" or v == "None":
                    r[k] = None
                    continue
                if k in ("surah", "ayah", "channels", "max_clip_run"):
                    try:
                        r[k] = int(float(v))
                    except Exception:
                        r[k] = None
                else:
                    try:
                        r[k] = float(v)
                    except Exception:
                        r[k] = None
            rows.append(r)
    return rows


def _num(row, key):
    v = row.get(key)
    return v if v is not None and isinstance(v, (int, float)) and np.isfinite(v) else None


def per_reciter_baselines(rows):
    """median of selected features per reciter (robust to outliers)."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        groups[r["reciter"]].append(r)
    base = {}
    for rec, g in groups.items():
        b = {}
        for feat in ("snr_db", "spectral_flatness", "silence_ratio",
                     "clipping_fraction", "dc_offset", "rms_dbfs"):
            vals = [_num(r, feat) for r in g]
            vals = [v for v in vals if v is not None]
            b[feat] = float(np.median(vals)) if vals else None
        b["_n"] = len(g)
        base[rec] = b
    return base


def per_file_flags(row, base, cfg):
    hard, review = [], []
    if row.get("error") and row["error"] != "None":
        return [row["error"]], review

    sr = _num(row, "sample_rate")
    if sr is not None and sr < cfg["min_sample_rate"]:
        hard.append("low_sample_rate:%d" % sr)
    elif sr is not None and sr < cfg["target_sample_rate"]:
        review.append("reduced_sample_rate:%d" % sr)

    br = _num(row, "avg_bitrate_kbps")
    if br is not None and br < cfg["min_bitrate_kbps"]:
        review.append("low_bitrate:%.0fkbps" % br)

    # corruption / no speech
    peak = _num(row, "peak_linear")
    orf = _num(row, "over_range_fraction")
    if (peak is not None and peak > cfg["peak_linear_reject"]) or \
       (orf is not None and orf > cfg["over_range_reject"]):
        hard.append("over_range:peak=%.2f,frac=%.4f" % (peak or -1, orf or -1))
    act = _num(row, "active_rms_dbfs")
    if act is not None and act < cfg["min_active_rms_dbfs"]:
        hard.append("near_silent:%.1fdB" % act)
    adur = _num(row, "active_duration_s")
    if adur is not None and adur < cfg["min_active_duration_s"]:
        hard.append("no_usable_speech")

    cf = _num(row, "clipping_fraction")
    if cf is not None:
        if cf >= cfg["clip_fraction_reject"]:
            hard.append("clipping:%.3f" % cf)
        elif cf >= cfg["clip_fraction_review"]:
            review.append("clipping:%.4f" % cf)
    cr = _num(row, "max_clip_run")
    if cr is not None:
        if cr >= cfg["clip_run_reject"]:
            hard.append("clipping_run:%d" % cr)
        elif cr >= cfg["clip_run_review"]:
            review.append("clipping_run:%d" % cr)

    # per-reciter-relative soft flags
    snr = _num(row, "snr_db")
    if snr is not None:
        if snr < cfg["snr_reject_db"]:
            hard.append("low_snr:%.1f" % snr)
        else:
            med = base.get(row["reciter"], {}).get("snr_db")
            if med and snr < cfg["snr_review_ratio"] * med and snr < cfg["snr_review_abs_db"]:
                review.append("low_snr_for_reciter:%.1f(med %.1f)" % (snr, med))

    fl = _num(row, "spectral_flatness")
    if fl is not None:
        med = base.get(row["reciter"], {}).get("spectral_flatness")
        if med is not None and fl > cfg["flatness_review_abs"] and fl > cfg["flatness_review_factor"] * med:
            review.append("flatness_for_reciter:%.3f(med %.3f)" % (fl, med))

    sil = _num(row, "silence_ratio")
    if sil is not None:
        if sil > cfg["silence_review_abs"]:
            med = base.get(row["reciter"], {}).get("silence_ratio")
            if med is not None and sil > cfg["silence_review_factor"] * max(med, 0.02):
                review.append("silence:%.2f(med %.2f)" % (sil, med))
            elif med is None:
                review.append("silence:%.2f" % sil)
    for side in ("leading_silence_s", "trailing_silence_s"):
        v = _num(row, side)
        if v is not None and v > cfg["edge_silence_review_s"]:
            review.append("%s:%.1fs" % (side.replace("_silence_s", ""), v))

    dc = _num(row, "dc_offset")
    if dc is not None and abs(dc) > cfg["dc_offset_review"]:
        review.append("dc_offset:%.3f" % dc)

    return hard, review


def cross_ayah_flags(rows, cfg):
    """Same-ayah duration comparison (identical text; the only fair cross-reciter
    check). Two-sided but deliberately wide, and review-only."""
    from collections import defaultdict
    groups = defaultdict(list)
    for r in rows:
        if _num(r, "surah") is not None and _num(r, "ayah") is not None:
            groups[(int(r["surah"]), int(r["ayah"]))].append(r)
    flags = defaultdict(list)
    lo, hi = cfg["duration_ratio_review"]
    for key, grp in groups.items():
        durs = [_num(r, "duration_s") for r in grp]
        durs = [d for d in durs if d]
        if len(durs) < cfg["cross_min_group"]:
            continue
        med = float(np.median(durs))
        for r in grp:
            d = _num(r, "duration_s")
            if d and med and not (lo <= d / med <= hi):
                flags[r["path"]].append("duration_ratio:%.2f" % (d / med))
    return flags


def decide(rows, cfg, baseline_rows=None):
    base = per_reciter_baselines(baseline_rows if baseline_rows is not None else rows)
    cross = cross_ayah_flags(rows, cfg)
    decisions = []
    for r in rows:
        hard, review = per_file_flags(r, base, cfg)
        review = list(dict.fromkeys(review + cross.get(r["path"], [])))
        d = "reject" if hard else ("review" if review else "accept")
        decisions.append({"path": r["path"], "rel_path": r.get("rel_path"),
                          "reciter": r.get("reciter"), "surah": r.get("surah"),
                          "ayah": r.get("ayah"), "decision": d,
                          "hard": hard, "review": review})
    return decisions, base


def write_outputs(decisions, base, outdir, cfg):
    os.makedirs(outdir, exist_ok=True)
    with open(os.path.join(outdir, "baselines.json"), "w") as f:
        json.dump(base, f, indent=2)
    with open(os.path.join(outdir, "decisions.jsonl"), "w") as f:
        for d in decisions:
            f.write(json.dumps(d) + "\n")
    with open(os.path.join(outdir, "decisions.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["reciter", "surah", "ayah", "decision", "hard_reasons", "review_reasons", "path"])
        for d in decisions:
            w.writerow([d["reciter"], d["surah"], d["ayah"], d["decision"],
                        ";".join(d["hard"]), ";".join(d["review"]), d["path"]])

    from collections import Counter
    n = len(decisions)
    by = {k: sum(1 for d in decisions if d["decision"] == k) for k in ("accept", "review", "reject")}
    lines = ["# Sample audit summary  (per-reciter-relative, conservative)", "",
             "Files: %d  ->  accept %d (%.1f%%) | review %d (%.1f%%) | reject %d (%.1f%%)"
             % (n, by["accept"], 100.0 * by["accept"] / max(n, 1),
                by["review"], 100.0 * by["review"] / max(n, 1),
                by["reject"], 100.0 * by["reject"] / max(n, 1)), "",
             "## Per reciter", "", "| reciter | n | accept | review | reject | median SNR | median flatness |",
             "|---|---|---|---|---|---|---|"]
    for rec in sorted({d["reciter"] for d in decisions}):
        sub = [d for d in decisions if d["reciter"] == rec]
        b = base.get(rec, {})
        lines.append("| %s | %d | %d | %d | %d | %.1f | %.4f |" % (
            rec, len(sub),
            sum(1 for d in sub if d["decision"] == "accept"),
            sum(1 for d in sub if d["decision"] == "review"),
            sum(1 for d in sub if d["decision"] == "reject"),
            b.get("snr_db") if b.get("snr_db") is not None else -1,
            b.get("spectral_flatness") if b.get("spectral_flatness") is not None else -1))
    hard_c, rev_c = Counter(), Counter()
    for d in decisions:
        for h in d["hard"]:
            hard_c[h.split(":")[0]] += 1
        for v in d["review"]:
            rev_c[v.split(":")[0]] += 1
    lines += ["", "## Reject reasons", ""] + ([f"- `{k}`: {v}" for k, v in hard_c.most_common()] or ["- none"])
    lines += ["", "## Review reasons", ""] + ([f"- `{k}`: {v}" for k, v in rev_c.most_common()] or ["- none"])
    with open(os.path.join(outdir, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return by


def cmd_extract(args):
    from features import extract_many, FEATURE_COLUMNS
    with open(args.files) as f:
        paths = [ln.strip() for ln in f if ln.strip()]
    print("extracting %d files with %d workers" % (len(paths), args.workers), flush=True)

    def prog(i, total):
        if i % 25 == 0 or i == total:
            print("  %d/%d" % (i, total), flush=True)

    rows = extract_many(paths, workers=args.workers, progress=prog)
    cols = ["path", "rel_path", "reciter", "surah", "ayah", "error", "spectral_error",
            "decode_backend"] + FEATURE_COLUMNS
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    print("wrote", args.out)


def cmd_decide(args):
    rows = load_features(args.features)
    baseline_rows = load_features(args.baseline_features) if args.baseline_features else None
    decisions, base = decide(rows, CONFIG, baseline_rows)
    by = write_outputs(decisions, base, args.outdir, CONFIG)
    print("decisions:", by)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("extract")
    p1.add_argument("--files", required=True)
    p1.add_argument("--out", required=True)
    p1.add_argument("--workers", type=int, default=8)
    p1.set_defaults(func=cmd_extract)
    p2 = sub.add_parser("decide")
    p2.add_argument("--features", required=True)
    p2.add_argument("--outdir", required=True)
    p2.add_argument("--baseline-features", default=None,
                    help="optional feature CSV used to build per-reciter baselines "
                         "(e.g. a reference set); defaults to --features itself")
    p2.set_defaults(func=cmd_decide)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
