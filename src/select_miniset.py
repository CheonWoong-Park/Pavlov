"""Select mini-set binaries for the Gate-0 pipeline test.

Picks projects whose O0 binaries are ALL fully contained in locally available
zip volumes (so dataset-coverage yield is meaningful) and which have >= min C
records in the locally available dataset shards. Extracts the binaries.

Usage:
  select_miniset.py --vols DIR --sizes SIZES --shards-glob GLOB --out-dir DIR \
      [--opt O0] [--n-projects 10] [--min-c-records 5]
"""

import argparse
import glob
import json
import os
import sys
from collections import defaultdict

import pyarrow as pa

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from zipsplit_extract import SplitVolumes, parse_central_directory, entry_available, extract_entry
from match_functions import project_of_binary, opt_of_binary


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vols", required=True)
    ap.add_argument("--sizes", required=True)
    ap.add_argument("--shards-glob", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--opt", default="O0")
    ap.add_argument("--n-projects", type=int, default=10)
    ap.add_argument("--min-c-records", type=int, default=5)
    ap.add_argument("--max-binary-mb", type=int, default=30)
    args = ap.parse_args()

    vols = SplitVolumes(args.vols, args.sizes)
    entries = parse_central_directory(vols)

    # group archive entries by (project, opt)
    proj_bins = defaultdict(list)
    for e in entries:
        base = os.path.basename(e["name"])
        if not base or e["name"].endswith("/"):
            continue
        proj = project_of_binary(base)
        opt = opt_of_binary(base)
        if proj and opt == args.opt:
            proj_bins[proj].append(e)

    # C record counts per project from available shards
    c_count = defaultdict(int)
    for sp in sorted(glob.glob(args.shards_glob)):
        with pa.memory_map(sp) as src:
            t = pa.ipc.open_stream(src).read_all()
        for n, f in zip(t["name"].to_pylist(), t["file"].to_pylist()):
            if f.endswith(".c") and "::" not in n:
                c_count[f.lstrip("/").split("/", 1)[0]] += 1

    picked = []
    for proj, bins in sorted(proj_bins.items(), key=lambda kv: -c_count.get(kv[0], 0)):
        if c_count.get(proj, 0) < args.min_c_records:
            continue
        if not all(entry_available(vols, e) for e in bins):
            continue
        if any(e["uncomp"] > args.max_binary_mb * 1e6 for e in bins):
            continue
        picked.append((proj, bins))
        if len(picked) >= args.n_projects:
            break

    os.makedirs(args.out_dir, exist_ok=True)
    manifest = []
    for proj, bins in picked:
        for e in bins:
            dest = extract_entry(vols, e, args.out_dir)
            flat = os.path.join(args.out_dir, os.path.basename(e["name"]))
            if dest != flat:
                os.replace(dest, flat)
            manifest.append({"project": proj, "binary": os.path.basename(e["name"]),
                             "c_records": c_count[proj], "size": e["uncomp"]})
            print(f"{proj}  {os.path.basename(e['name'])}  c_records={c_count[proj]}", file=sys.stderr)
    json.dump(manifest, open(os.path.join(args.out_dir, "manifest.json"), "w"), indent=1)
    print(f"projects={len(picked)} binaries={len(manifest)}")


if __name__ == "__main__":
    main()
