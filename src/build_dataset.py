"""Build stage-1 training examples from matched (pseudo, source) pairs.

Each matched record -> {input: ghidra pseudo, target: anonymized skeleton,
meta...}. Drops pairs whose skeleton fails to re-parse or whose pseudo fails
basic sanity. Optional token-length filter when a tokenizer is available.

Usage:
  build_dataset.py --matched matched.jsonl --out train.jsonl \
      [--tokenizer Qwen/Qwen2.5-Coder-7B --max-tokens 4096]
"""

import argparse
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from anonymize import anonymize_c, parses_clean


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--matched", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tokenizer", default=None)
    ap.add_argument("--max-tokens", type=int, default=4096)
    args = ap.parse_args()

    tok = None
    if args.tokenizer:
        from transformers import AutoTokenizer
        tok = AutoTokenizer.from_pretrained(args.tokenizer, trust_remote_code=True)

    stats = {"in": 0, "kept": 0, "skel_parse_fail": 0, "too_long": 0,
             "empty": 0, "src_parse_fail": 0}
    with open(args.out, "w") as out:
        for line in open(args.matched):
            stats["in"] += 1
            r = json.loads(line)
            pseudo, code = r["pseudo"], r["code"]
            if not pseudo.strip() or not code.strip():
                stats["empty"] += 1
                continue
            if not parses_clean(code):
                stats["src_parse_fail"] += 1
                continue
            skel, mapping = anonymize_c(code)
            if not parses_clean(skel):
                stats["skel_parse_fail"] += 1
                continue
            if tok:
                n = len(tok(pseudo)["input_ids"]) + len(tok(skel)["input_ids"])
                if n > args.max_tokens:
                    stats["too_long"] += 1
                    continue
            else:
                if (len(pseudo) + len(skel)) // 3 > args.max_tokens:  # rough chars->tokens
                    stats["too_long"] += 1
                    continue
            out.write(json.dumps({
                "input": pseudo, "target": skel, "mapping": mapping,
                "project": r.get("project"), "binary": r.get("binary"),
                "opt": r.get("opt"), "func_name": r.get("func_name"),
                "file": r.get("file"), "source": code,
            }) + "\n")
            stats["kept"] += 1
    print(json.dumps(stats, indent=1))


if __name__ == "__main__":
    main()
