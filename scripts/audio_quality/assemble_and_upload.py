"""
Assemble the filtered dataset and upload it to GCS.

Audio never goes into git. At the end of a run, this script mirrors the accepted
(and optionally review) files into a staging tree and rsyncs it to the bucket.

Staging layout:
    <stage>/accepted/<reciter>/<SSSAAA>.mp3
    <stage>/review/<reciter>/<SSSAAA>.mp3      (only with --include review)
    <stage>/manifests/decisions.csv
    <stage>/manifests/baselines.json
    <stage>/manifests/summary.md

Files are hard-linked into the staging tree when possible (same filesystem, no
extra disk use); otherwise they are copied.

Examples:
    # assemble only (no upload), accepted files only
    python3 assemble_and_upload.py --decisions reports/sample1/decisions.jsonl \
        --stage /tmp/opencode/quran_filtered_stage

    # assemble accepted + review, then upload
    python3 assemble_and_upload.py --decisions reports/all/decisions.jsonl \
        --include accepted,review --reports reports/all \
        --stage /tmp/opencode/quran_filtered_stage --upload

    # full pipeline over 56k files: use paths.jsonl from the full run
    python3 assemble_and_upload.py --decisions reports/all/decisions.jsonl \
        --stage /content/quran_filtered_stage --upload
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

DEST_DEFAULT = "gs://sheikh-fitzgerald-backup/ARABIC_DATA/Quran_Filtered_Audio_Data"


def link_or_copy(src: str, dst: str) -> str:
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        return "exists"
    try:
        os.link(src, dst)
        return "linked"
    except OSError:
        shutil.copy2(src, dst)
        return "copied"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decisions", required=True, help="decisions.jsonl from audit.py decide")
    ap.add_argument("--stage", required=True, help="staging directory")
    ap.add_argument("--dest", default=DEST_DEFAULT, help="GCS destination prefix")
    ap.add_argument("--include", default="accepted",
                    help="comma-separated: accepted,review")
    ap.add_argument("--reports", default=None,
                    help="directory whose reports are copied into <stage>/manifests")
    ap.add_argument("--upload", action="store_true", help="run gsutil rsync after staging")
    ap.add_argument("--dry-run", action="store_true", help="assemble but never upload")
    args = ap.parse_args()

    wanted = {s.strip() for s in args.include.split(",") if s.strip()}
    unknown = wanted - {"accepted", "review"}
    if unknown:
        sys.exit("unknown --include value(s): %s" % ", ".join(sorted(unknown)))

    counts = {k: 0 for k in wanted}
    missing = 0
    total = 0
    with open(args.decisions) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            total += 1
            if d["decision"] not in ("accept", "review"):
                continue
            setname = "accepted" if d["decision"] == "accept" else "review"
            if setname not in wanted:
                continue
            src = d["path"]
            if not os.path.exists(src):
                missing += 1
                continue
            rel = os.path.join(d["reciter"], os.path.basename(src))
            dst = os.path.join(args.stage, setname, rel)
            link_or_copy(src, dst)
            counts[setname] += 1

    # provenance
    man = os.path.join(args.stage, "manifests")
    os.makedirs(man, exist_ok=True)
    if args.reports and os.path.isdir(args.reports):
        for name in ("decisions.csv", "decisions.jsonl", "baselines.json", "summary.md"):
            p = os.path.join(args.reports, name)
            if os.path.exists(p):
                shutil.copy2(p, os.path.join(man, name))
    print("scanned %d decisions; staged %s; missing sources: %d"
          % (total, counts, missing))
    print("stage:", args.stage)

    if not args.upload or args.dry_run:
        print("not uploading (dry-run). To upload:\n"
              "  gsutil -m rsync -r %s %s" % (args.stage, args.dest))
        return

    dest = args.dest.rstrip("/") + "/"
    print("uploading to", dest)
    rc = subprocess.call(["gsutil", "-m", "rsync", "-r", args.stage, dest])
    sys.exit(rc)


if __name__ == "__main__":
    main()
