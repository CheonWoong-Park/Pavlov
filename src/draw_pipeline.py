"""Pavlov 실험 파이프라인 아키텍처 다이어그램."""
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

fig, ax = plt.subplots(figsize=(13, 8.5))
ax.set_xlim(0, 100); ax.set_ylim(0, 100); ax.axis("off")

C = dict(data="#e8eef5", ghi="#dfeee0", arm_a="#f6dada", arm_d="#d6e6f4",
         filler="#efe4d4", out="#e4e4e4", metric="#fff6cc", gold="#dfeee0")

def box(x, y, w, h, text, fc, fs=10, bold=False, ec="#333"):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.2",
                                fc=fc, ec=ec, lw=1.3))
    ax.text(x+w/2, y+h/2, text, ha="center", va="center", fontsize=fs,
            fontweight="bold" if bold else "normal")

def arrow(x1, y1, x2, y2, color="#333", style="-|>", lw=1.6, ls="-"):
    ax.add_patch(FancyArrowPatch((x1, y1), (x2, y2), arrowstyle=style,
                 mutation_scale=14, color=color, lw=lw, linestyle=ls,
                 connectionstyle="arc3,rad=0"))

def band(y, label):
    ax.text(1.5, y, label, ha="left", va="center", fontsize=11, fontweight="bold",
            color="#444", rotation=90)

# ---------- Phase 1: Data preparation ----------
band(86, "1. Data")
box(8, 90, 17, 7, "Binary\n(decompile-bench)", C["data"])
box(33, 90, 17, 7, "Ghidra\nanalyzeHeadless", C["data"])
box(58, 90, 18, 7, "Pseudocode\n(model input)", C["data"], bold=True)
arrow(25, 93.5, 33, 93.5); arrow(50, 93.5, 58, 93.5)

box(8, 80, 17, 7, "C source", C["gold"])
box(33, 80, 17, 7, "tree-sitter\nanonymize (AST)", C["gold"])
box(58, 80, 18, 7, "Gold skeleton\n(target / GT)", C["gold"], bold=True)
arrow(25, 83.5, 33, 83.5); arrow(50, 83.5, 58, 83.5)

# ---------- Phase 2: Stage 1 (the experiment) ----------
band(56, "2. Stage 1")
# input feeds both arms
arrow(67, 90, 67, 67.5, color="#1b5"); ax.text(68.5, 74, "pseudocode", fontsize=8, color="#1b5")
arrow(67, 70, 38, 64); arrow(67, 70, 38, 53)
box(12, 60, 52, 7, "A arm:  Qwen2.5-Coder-7B + LoRA   —   autoregressive (standard CE)", C["arm_a"], fs=10, bold=True)
box(12, 49, 52, 7, "D arm:  DiffuCoder-7B-Base + LoRA   —   masked diffusion (LLaDA)", C["arm_d"], fs=10, bold=True)
ax.text(38, 45.5, "same backbone family  /  same data  /  same LoRA (r32, a64)  =  controlled comparison",
        ha="center", fontsize=8.5, style="italic", color="#555")
box(72, 54.5, 20, 8, "Generated\nskeleton", C["out"], bold=True)
arrow(64, 63.5, 72, 59); arrow(64, 52.5, 72, 57)

# ---------- Phase 3: Stage 2 + metrics ----------
band(26, "3. Eval")
arrow(82, 54.5, 82, 34, ls="-")
box(60, 26, 24, 7, "Frozen filler\nQwen2.5-Coder-7B-Instruct", C["filler"], fs=9, bold=True)
arrow(82, 34, 78, 33)
box(60, 14, 24, 7, "Restored C", C["out"], bold=True)
arrow(72, 26, 72, 21)

# metrics panel
mx, my, mw, mh = 6, 6, 46, 32
ax.add_patch(FancyBboxPatch((mx, my), mw, mh, boxstyle="round,pad=0.5,rounding_size=1.5",
                            fc=C["metric"], ec="#caa000", lw=1.5))
ax.text(mx+mw/2, my+mh-2.5, "Metrics", ha="center", fontsize=11, fontweight="bold")
lines = [
    ("* AST edit distance", "Generated skeleton  vs  Gold skeleton   (primary)"),
    ("parse rate", "fraction of Generated skeletons that parse as C"),
    ("skeleton violation", "filler output (re-anonymized)  vs  Generated skeleton"),
    ("re-executability", "Restored C : gcc compile + run, pass c_test"),
]
for i, (k, v) in enumerate(lines):
    yy = my+mh-7.5 - i*6.2
    ax.text(mx+2.5, yy, k, fontsize=9.5, fontweight="bold", va="center")
    ax.text(mx+2.5, yy-2.6, v, fontsize=8, va="center", color="#444")

# metric feed arrows (dashed)
arrow(72, 54.5, 52, 38, color="#caa000", lw=1.2, ls="--")   # gen skeleton -> metrics
arrow(76, 80, 52, 36, color="#1b5", lw=1.2, ls="--")        # gold -> AST distance
ax.text(54, 40, "gen skeleton", fontsize=7, color="#caa000")
ax.text(54, 34.5, "gold", fontsize=7, color="#1b5")

ax.set_title("Pavlov — Experiment Pipeline (Diffusion vs Autoregressive AST skeleton restoration)",
             fontsize=13, fontweight="bold", pad=12)
plt.tight_layout()
plt.savefig("eval_pull_s0/fig_pipeline.png", dpi=160, bbox_inches="tight")
print("저장 eval_pull_s0/fig_pipeline.png")
