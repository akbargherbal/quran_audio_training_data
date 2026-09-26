"""Build a balanced sample of (ayah x 9 reciters) for filter calibration.

Applies the same spirit as quran_ayah_filtering_specs.md (drop <5-word ayat)
and deliberately includes ayat that contain known structural anomalies, so the
fidelity gate is exercised.
"""
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = "/content/Quran_Audio_Data"
TEXT = os.path.join(REPO, "quran_text", "quran-simple.txt")
OUT = os.path.join(REPO, "reports", "sample_files.txt")

KNOWN_ANOMALY_AYAT = [(17, 83), (10, 29), (59, 9), (2, 167), (2, 122), (2, 66), (2, 12)]


def load_ayat():
    ayat = []
    with open(TEXT, encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("|")
            if len(parts) < 3:
                continue
            s, a, text = int(parts[0]), int(parts[1]), "|".join(parts[2:])
            if len(text.split()) >= 5:
                ayat.append((s, a))
    return ayat


def reciters():
    return sorted(d for d in os.listdir(DATA)
                  if os.path.isdir(os.path.join(DATA, d)) and d != "extras"
                  and not d.startswith("."))


def main(n_random=45, seed=7):
    rnd = random.Random(seed)
    pool = load_ayat()
    sample = list(KNOWN_ANOMALY_AYAT)
    chosen = set(sample)
    while len(sample) < n_random:
        s, a = rnd.choice(pool)
        if (s, a) not in chosen:
            chosen.add((s, a))
            sample.append((s, a))
    recs = reciters()
    paths = []
    missing = 0
    for s, a in sample:
        for r in recs:
            p = os.path.join(DATA, r, "%03d%03d.mp3" % (s, a))
            if os.path.exists(p):
                paths.append(p)
            else:
                missing += 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write("\n".join(paths) + "\n")
    print("ayat=%d reciters=%d files=%d missing=%d" % (len(sample), len(recs), len(paths), missing))
    print("wrote", OUT)


if __name__ == "__main__":
    main(*(int(x) for x in sys.argv[1:]) if len(sys.argv) > 1 else ())
