"""Gate 3 — skeleton parse rate (plan §6.2, §8).

Reads generated skeletons (from eval_generate.py) and reports the fraction that
parse cleanly as C (tree-sitter, no ERROR/MISSING nodes), reusing
anonymize.parses_clean. Gate 3 passes when parse rate >= 0.80.

Reports overall and per optimization level, since structure recovery typically
degrades with higher -O. Compares two arms side by side if two files are given.

  python src/eval_gate3.py checkpoints_from_a100/diff_s0/gen.jsonl [ar.jsonl ...]
"""

import argparse
import json
from collections import defaultdict

from anonymize import parses_clean

GATE = 0.80


def score(path):
    rows = [json.loads(l) for l in open(path)]
    by_opt = defaultdict(lambda: [0, 0])  # opt -> [ok, total]
    total_ok = 0
    for r in rows:
        ok = parses_clean(r["skeleton"])
        by_opt[r["type"]][0] += int(ok)
        by_opt[r["type"]][1] += 1
        total_ok += int(ok)
    return rows, by_opt, total_ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="generated-skeleton jsonl file(s)")
    args = ap.parse_args()

    for path in args.files:
        rows, by_opt, total_ok = score(path)
        n = len(rows)
        rate = total_ok / n if n else 0.0
        print(f"\n=== {path} ===")
        print(f"  parse rate: {total_ok}/{n} = {rate:.1%}  "
              f"[{'PASS' if rate >= GATE else 'FAIL'} vs Gate 3 {GATE:.0%}]")
        for opt in sorted(by_opt):
            ok, tot = by_opt[opt]
            print(f"    {opt}: {ok}/{tot} = {ok/tot:.1%}")


if __name__ == "__main__":
    main()
