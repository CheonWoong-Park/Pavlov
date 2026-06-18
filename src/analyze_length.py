"""길이 실험 분석: held-out O0 240, bin별 A/D AST edit distance + log(size) 회귀.

  python src/analyze_length.py \
    --eval data/length_exp/heldout_O0_240.json \
    --diff eval_pull_len/len_diff_gen.jsonl \
    --ar   eval_pull_len/len_ar_gen.jsonl \
    --out  eval_pull_len/length_result.png
"""
import argparse, json
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import tree_sitter_c
from tree_sitter import Language, Parser
import zss
from anonymize import anonymize_c

_LANG = Language(tree_sitter_c.language()); _P = Parser(_LANG)
class Node:
    __slots__ = ("label", "children")
    def __init__(self, l, c): self.label = l; self.children = c
def to_tree(code):
    t = _P.parse(code.encode())
    def b(n): return Node(n.type, [b(c) for c in n.children if c.is_named])
    return b(t.root_node)
def size(n): return 1 + sum(size(c) for c in n.children)
def dist(a, b):
    return zss.simple_distance(a, b, get_children=lambda n: n.children,
        get_label=lambda n: n.label, label_dist=lambda x, y: 0 if x == y else 1)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", required=True)
    ap.add_argument("--diff", required=True)
    ap.add_argument("--ar", required=True)
    ap.add_argument("--out", default="eval_pull_len/length_result.png")
    args = ap.parse_args()

    items = {d["task_id"]: d for d in json.load(open(args.eval))}
    gold = {}
    for tid, d in items.items():
        g = to_tree(anonymize_c(d["c_func"])[0]); gold[tid] = (g, size(g))

    def score(path):
        out = {}
        for l in open(path):
            r = json.loads(l); tid = r["task_id"]
            if tid not in gold: continue
            g, gs = gold[tid]
            try: out[tid] = dist(to_tree(r["skeleton"]), g) / gs
            except Exception: out[tid] = 1.0
        return out
    A, D = score(args.ar), score(args.diff)
    keys = sorted(set(A) & set(D))
    print(f"채점 n={len(keys)}  (ar {len(A)}, diff {len(D)})")
    sz = np.array([items[k]["node_size"] for k in keys])
    da = np.array([A[k] for k in keys]); dd = np.array([D[k] for k in keys])
    print(f"전체  A={da.mean():.3f}  D={dd.mean():.3f}  (median A={np.median(da):.3f} D={np.median(dd):.3f})")
    print(f"collapse(>1.0)  A={(da>1).sum()}  D={(dd>1).sum()}")

    # bin별 (eval json의 bin 사용)
    bins = sorted(set(items[k]["bin"] for k in keys), key=lambda b: int(b.split("-")[0]))
    xs, ma, md, mea, med_a, med_d, ea, ed = [], [], [], [], [], [], [], []
    print("\nbin              n   A_mean A_med  D_mean D_med  gap(A-D)  Dcollapse")
    for b in bins:
        idx = [i for i, k in enumerate(keys) if items[k]["bin"] == b]
        a, d = da[idx], dd[idx]
        # bootstrap 95% CI of mean
        def ci(x):
            bs = [np.mean(np.random.choice(x, len(x))) for _ in range(2000)]
            return np.percentile(bs, 2.5), np.percentile(bs, 97.5)
        xs.append(np.mean([sz[i] for i in idx]))
        ma.append(a.mean()); md.append(d.mean())
        ea.append(ci(a)); ed.append(ci(d))
        med_a.append(np.median(a)); med_d.append(np.median(d))
        print(f"{b:<14} {len(idx):>3}  {a.mean():.3f} {np.median(a):.3f}  {d.mean():.3f} {np.median(d):.3f}  {a.mean()-d.mean():+.3f}    {(d>1).sum()}/{len(idx)}")

    # 회귀: Δ=(A-D) ~ log(size), 기울기 β + cluster bootstrap CI
    delta = da - dd; x = np.log(sz)
    beta = np.polyfit(x, delta, 1)[0]
    bs = []
    for _ in range(5000):
        s = np.random.choice(len(keys), len(keys))
        bs.append(np.polyfit(x[s], delta[s], 1)[0])
    lo, hi = np.percentile(bs, 2.5), np.percentile(bs, 97.5)
    print(f"\n회귀  Δ=(A-D) ~ log(node):  β={beta:+.4f}  95%CI [{lo:+.4f}, {hi:+.4f}]")
    print("  해석:", "β>0 & CI>0 → 길수록 D 우위" if lo > 0 else
          ("β<0 & CI<0 → 길수록 A 우위" if hi < 0 else "CI가 0 포함 → 길이효과 유의하지 않음 (대등)"))

    # 그래프
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    xr = range(len(bins))
    ax1.errorbar(xr, ma, yerr=[[m-c[0] for m,c in zip(ma,ea)],[c[1]-m for m,c in zip(ma,ea)]],
                 fmt="o-", color="#c0392b", capsize=4, label="A (Autoregressive)")
    ax1.errorbar(xr, md, yerr=[[m-c[0] for m,c in zip(md,ed)],[c[1]-m for m,c in zip(md,ed)]],
                 fmt="s-", color="#2471a3", capsize=4, label="D (Diffusion)")
    ax1.set_xticks(list(xr)); ax1.set_xticklabels(bins, fontsize=8, rotation=15)
    ax1.set_xlabel("function size (gold AST node count, bin)")
    ax1.set_ylabel("AST edit distance (lower=better)")
    ax1.set_title("AST edit distance by size (95% CI)"); ax1.legend(); ax1.grid(alpha=0.3)

    ax2.scatter(sz, delta, s=12, alpha=0.4, color="#555")
    xx = np.linspace(sz.min(), sz.max(), 100)
    ax2.plot(xx, np.polyval(np.polyfit(x, delta, 1), np.log(xx)), color="#2471a3", lw=2)
    ax2.axhline(0, color="k", lw=0.8)
    ax2.set_xlabel("function size (node count)")
    ax2.set_ylabel("Δ = A_dist - D_dist  (positive = D better)")
    ax2.set_title(f"β={beta:+.4f}  95%CI [{lo:+.3f},{hi:+.3f}]"); ax2.grid(alpha=0.3)
    plt.tight_layout(); plt.savefig(args.out, dpi=150)
    print(f"\n저장 {args.out}")

if __name__ == "__main__":
    main()
