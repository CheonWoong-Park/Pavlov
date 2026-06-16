"""End-to-end re-executability and skeleton violation rate (plan §6.2).

For each filled C function (eval_filler.py output), compile it together with the
decompile-eval test harness (`c_test`, an assert-based main) and run the binary
under a sandbox: wall-clock timeout plus CPU / address-space / file-size rlimits,
no shell. A pass means it compiles and every assert succeeds (exit 0).

Re-executability = pass fraction. Also re-anonymizes each filled function and
compares it to the stage-1 skeleton to measure how often the filler altered
structure instead of only filling it (skeleton violation rate).

This compiles and runs model-generated code. Run it only on an isolated machine
(container / VM), never on anything you care about.

  python src/eval_reexec.py --filled f.jsonl --gen gen.jsonl --eval-json <ghidra.json>
"""

import argparse
import json
import os
import resource
import subprocess
import tempfile
from collections import defaultdict

from anonymize import anonymize_c, parses_clean


def _limits(cpu_s, mem_mb):
    def apply():
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_s, cpu_s))
        m = mem_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (m, m))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
    return apply


def compile_run(code, c_test, cc, timeout, cpu_s, mem_mb):
    """Return one of: pass, run_fail, run_timeout, compile_error, compile_timeout."""
    with tempfile.TemporaryDirectory() as d:
        src, binp = os.path.join(d, "prog.c"), os.path.join(d, "prog")
        open(src, "w").write(code + "\n" + c_test + "\n")
        try:
            cp = subprocess.run([cc, "-O0", "-w", "-o", binp, src, "-lm"],
                                capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return "compile_timeout"
        if cp.returncode != 0:
            return "compile_error"
        try:
            rp = subprocess.run([binp], capture_output=True, timeout=timeout,
                                preexec_fn=_limits(cpu_s, mem_mb))
        except subprocess.TimeoutExpired:
            return "run_timeout"
        return "pass" if rp.returncode == 0 else "run_fail"


def violated(filled_code, stage1_skeleton):
    """True if the filler changed structure: filled code doesn't parse, or its
    re-anonymized skeleton differs from the stage-1 skeleton."""
    if not parses_clean(filled_code):
        return True
    skel2, _ = anonymize_c(filled_code)
    norm = lambda s: "\n".join(l.strip() for l in s.splitlines() if l.strip())
    return norm(skel2) != norm(stage1_skeleton)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--filled", required=True, help="eval_filler output jsonl")
    ap.add_argument("--eval-json", required=True, help="decompile-eval ghidra json (c_test)")
    ap.add_argument("--gen", help="stage-1 skeleton jsonl (enables skeleton violation rate)")
    ap.add_argument("--cc", default="gcc")
    ap.add_argument("--timeout", type=int, default=10, help="wall-clock seconds per compile/run")
    ap.add_argument("--cpu-s", type=int, default=5, help="RLIMIT_CPU seconds for the binary")
    ap.add_argument("--mem-mb", type=int, default=1024, help="RLIMIT_AS megabytes for the binary")
    ap.add_argument("--out", help="optional per-item result jsonl")
    args = ap.parse_args()

    ev = {(it["task_id"], it["type"]): it for it in json.load(open(args.eval_json))}
    filled = [json.loads(l) for l in open(args.filled)]
    skels = {}
    if args.gen:
        for l in open(args.gen):
            r = json.loads(l)
            skels[(r["task_id"], r["type"])] = r["skeleton"]

    by_opt = defaultdict(lambda: [0, 0])   # opt -> [pass, total]
    status_count = defaultdict(int)
    n_pass = n_viol = n_viol_total = 0
    outf = open(args.out, "w") if args.out else None

    for r in filled:
        key = (r["task_id"], r["type"])
        item = ev.get(key)
        status = compile_run(r["code"], item["c_test"], args.cc,
                             args.timeout, args.cpu_s, args.mem_mb) if item else "no_test"
        ok = status == "pass"
        by_opt[r["type"]][0] += int(ok)
        by_opt[r["type"]][1] += 1
        status_count[status] += 1
        n_pass += int(ok)
        viol = None
        if key in skels:
            viol = violated(r["code"], skels[key])
            n_viol += int(viol)
            n_viol_total += 1
        if outf:
            outf.write(json.dumps({"task_id": r["task_id"], "type": r["type"],
                                   "status": status, "violation": viol}) + "\n")
    if outf:
        outf.close()

    n = len(filled)
    print(f"=== {args.filled} ===")
    print(f"re-executability: {n_pass}/{n} = {n_pass/n:.1%}" if n else "no items")
    for opt in sorted(by_opt):
        ok, tot = by_opt[opt]
        print(f"    {opt}: {ok}/{tot} = {ok/tot:.1%}")
    print("  status breakdown:", dict(status_count))
    if n_viol_total:
        print(f"  skeleton violation rate: {n_viol}/{n_viol_total} = {n_viol/n_viol_total:.1%}")


if __name__ == "__main__":
    main()
