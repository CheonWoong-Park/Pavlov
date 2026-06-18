"""AST edit distance vs gold skeleton (plan §6.2, structural fidelity).

Parses the generated skeleton and the gold skeleton (anonymize_c of c_func) with
tree-sitter, then computes Zhang-Shasha tree edit distance over NODE TYPES only
(placeholder names ignored). Reports distance normalized by gold tree size, so
lower = structurally closer to the reference. Unlike parse rate this does not
saturate, so it discriminates the arms.

  python src/eval_ast_distance.py <gen.jsonl> --eval-json <ghidra.json>
"""
import argparse, json
import tree_sitter_c
from tree_sitter import Language, Parser
import zss
from collections import defaultdict
from anonymize import anonymize_c

_LANG = Language(tree_sitter_c.language())
_parser = Parser(_LANG)

class Node:
    __slots__ = ("label", "children")
    def __init__(self, label, children): self.label = label; self.children = children

def to_tree(code):
    t = _parser.parse(code.encode())
    def build(n): return Node(n.type, [build(c) for c in n.children if c.is_named])
    return build(t.root_node)

def size(n): return 1 + sum(size(c) for c in n.children)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("gen"); ap.add_argument("--eval-json", required=True)
    args = ap.parse_args()
    ev = {(i["task_id"], i["type"]): i for i in json.load(open(args.eval_json))}
    rows = [json.loads(l) for l in open(args.gen)]
    by_opt = defaultdict(list); alld = []
    for r in rows:
        item = ev.get((r["task_id"], r["type"]))
        if not item: continue
        try:
            gold, _ = anonymize_c(item["c_func"])
            tg = to_tree(gold); tp = to_tree(r["skeleton"])
        except Exception:
            continue
        d = zss.simple_distance(tg, tp, lambda x: x.children, lambda x: x.label,
                                lambda a, b: 0 if a == b else 1)
        nd = d / max(1, size(tg))      # normalize by gold size
        by_opt[r["type"]].append(nd); alld.append(nd)
    print(f"=== {args.gen} ===")
    print(f"  AST edit distance (정답 대비, 낮을수록 좋음): {sum(alld)/len(alld):.3f}  n={len(alld)}")
    for o in sorted(by_opt):
        v = by_opt[o]; print(f"    {o}: {sum(v)/len(v):.3f}  n={len(v)}")

if __name__ == "__main__": main()
