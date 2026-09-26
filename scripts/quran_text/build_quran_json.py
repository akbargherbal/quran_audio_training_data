#!/usr/bin/env python3
"""Convert Tanzil Quran text files to JSON keyed by the audio naming convention.

The audio dataset stores every ayah as ``<reciter>/SSSAAA.mp3`` (zero-padded
3-digit surah + 3-digit ayah). This converter produces a flat JSON object with
exactly those keys, so text and audio align by filename:

    {"001001": "<ayah text> ۝", "001002": "...", ...}

The Arabic End of Ayah sign (U+06DD, ۝), preceded by a space, is appended to
every ayah.

Input format (Tanzil):
    SSS|AAA|text
Blank lines and lines starting with ``#`` (the copyright block) are ignored.

Usage:
    python3 scripts/quran_text/build_quran_json.py --all
    python3 scripts/quran_text/build_quran_json.py quran_text/quran-simple.txt \\
        quran_text/quran-simple.json

Source text: Tanzil Project (https://tanzil.net), CC BY 3.0. See
quran_text/NOTICE-tanzil.txt for the required copyright notice.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

AYAH_END = "\u06dd"  # ۝ ARABIC END OF AYAH
AYAH_SUFFIX = " " + AYAH_END  # space-separated from the last word
LINE_RE = re.compile(r"^(\d{1,3})\|(\d{1,3})\|(.*)$")
HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TEXT_DIR = os.path.join(REPO, "quran_text")

DEFAULT_JOBS = [
    ("quran-simple.txt", "quran-simple.json"),
    ("quran-uthmani.txt", "quran-uthmani.json"),
]


def parse(path: str) -> dict:
    """Return {SSSAAA: text ending with U+06DD} for one Tanzil file."""
    ayat: dict[str, str] = {}
    with open(path, encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            line = raw.rstrip("\n")
            if not line or line.startswith("#"):
                continue
            m = LINE_RE.match(line)
            if not m:
                sys.exit("%s:%d: cannot parse line: %r" % (path, lineno, line[:80]))
            surah, ayah = int(m.group(1)), int(m.group(2))
            text = m.group(3).strip()
            if not (1 <= surah <= 114 and 1 <= ayah <= 286):
                sys.exit("%s:%d: out-of-range surah:ayah %d:%d" % (path, lineno, surah, ayah))
            key = "%03d%03d" % (surah, ayah)
            if key in ayat:
                sys.exit("%s:%d: duplicate key %s" % (path, lineno, key))
            if not text.endswith(AYAH_END):
                text += AYAH_SUFFIX
            ayat[key] = text
    return ayat


def write_json(ayat: dict, out_path: str) -> None:
    ordered = {k: ayat[k] for k in sorted(ayat)}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", nargs="?", help="input .txt (Tanzil format)")
    ap.add_argument("output", nargs="?", help="output .json")
    ap.add_argument("--all", action="store_true",
                    help="convert quran-simple and quran-uthmani under quran_text/")
    args = ap.parse_args()

    if args.all:
        jobs = [(os.path.join(TEXT_DIR, i), os.path.join(TEXT_DIR, o))
                for i, o in DEFAULT_JOBS]
    elif args.input and args.output:
        jobs = [(args.input, args.output)]
    else:
        ap.error("give INPUT OUTPUT or --all")

    for src, dst in jobs:
        ayat = parse(src)
        write_json(ayat, dst)
        n_end = sum(1 for v in ayat.values() if v.endswith(AYAH_END))
        print("%s -> %s  (%d ayat, %d end with U+06DD)"
              % (os.path.relpath(src), os.path.relpath(dst), len(ayat), n_end))


if __name__ == "__main__":
    main()
