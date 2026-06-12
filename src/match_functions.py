"""Match Ghidra-extracted pseudocode functions to decompile-bench source records.

Binary name convention : <user>[P]<repo>[P]build_<OPT>[P]<binname>
Dataset file field     : /<user>[P]<repo>/<path/in/repo>
Project key            : "<user>[P]<repo>"
Match key              : (project, function name). C only (file endswith .c,
                         name without '::').

Usage:
  match_functions.py --pseudo-dir DIR --shards-glob "....arrow" --out matched.jsonl
"""

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

import pyarrow as pa


def project_of_binary(binname):
    parts = binname.split("[P]")
    if len(parts) >= 3:
        return "[P]".join(parts[:2])
    return None


def opt_of_binary(binname):
    m = re.search(r"\[P\]build_(O[0-3s])\[P\]", binname)
    return m.group(1) if m else None


def load_c_records(shard_paths, projects=None):
    """index[(project, func_name)] -> list of {code, file}; C files only."""
    index = defaultdict(list)
    per_project = defaultdict(int)
    for sp in shard_paths:
        with pa.memory_map(sp) as src:
            t = pa.ipc.open_stream(src).read_all()
        names = t["name"].to_pylist()
        files = t["file"].to_pylist()
        codes = t["code"].to_pylist()
        for n, f, c in zip(names, files, codes):
            if not f.endswith(".c"):
                continue
            if "::" in n:
                continue
            seg = f.lstrip("/").split("/", 1)[0]
            if projects is not None and seg not in projects:
                continue
            # C names in dataset may still carry a signature — strip at '('
            fname = n.split("(")[0].strip()
            index[(seg, fname)].append({"code": c, "file": f})
            per_project[seg] += 1
    return index, per_project


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pseudo-dir", required=True)
    ap.add_argument("--shards-glob", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", default=None)
    args = ap.parse_args()

    pseudo_files = sorted(glob.glob(os.path.join(args.pseudo_dir, "*.pseudo.jsonl")))
    if not pseudo_files:
        sys.exit("no pseudo jsonl files found")

    projects = set()
    for pf in pseudo_files:
        p = project_of_binary(os.path.basename(pf).replace(".pseudo.jsonl", ""))
        if p:
            projects.add(p)
    print(f"binaries: {len(pseudo_files)}, projects: {len(projects)}", file=sys.stderr)

    shards = sorted(glob.glob(args.shards_glob))
    index, per_project = load_c_records(shards, projects)
    total_c_records = sum(per_project.values())
    print(f"shards: {len(shards)}, C records in attempted projects: {total_c_records}", file=sys.stderr)

    matched = 0
    ghidra_funcs = 0
    ghidra_named = 0  # excluding FUN_/auto names
    matched_keys = set()
    with open(args.out, "w") as out:
        for pf in pseudo_files:
            binname = os.path.basename(pf).replace(".pseudo.jsonl", "")
            proj = project_of_binary(binname)
            opt = opt_of_binary(binname)
            for line in open(pf):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ghidra_funcs += 1
                fname = rec["name"]
                if re.match(r"^(FUN_|_INIT_|_FINI_|__|_DT_|entry$)", fname):
                    continue
                ghidra_named += 1
                key = (proj, fname)
                if key in index:
                    src_rec = index[key][0]
                    out.write(json.dumps({
                        "project": proj, "binary": binname, "opt": opt,
                        "func_name": fname, "pseudo": rec["pseudo"],
                        "code": src_rec["code"], "file": src_rec["file"],
                    }) + "\n")
                    matched += 1
                    matched_keys.add(key)

    uniq_dataset_keys = len({k for k in index})
    report = {
        "binaries": len(pseudo_files),
        "projects": len(projects),
        "ghidra_functions_total": ghidra_funcs,
        "ghidra_functions_named": ghidra_named,
        "dataset_c_records_in_projects": total_c_records,
        "dataset_unique_keys": uniq_dataset_keys,
        "matched_pairs_written": matched,
        "matched_unique_keys": len(matched_keys),
        "yield_dataset_coverage": round(len(matched_keys) / max(1, uniq_dataset_keys), 4),
        "yield_ghidra_named_matched": round(len(matched_keys) / max(1, ghidra_named), 4),
    }
    print(json.dumps(report, indent=1))
    if args.report:
        json.dump(report, open(args.report, "w"), indent=1)


if __name__ == "__main__":
    main()
