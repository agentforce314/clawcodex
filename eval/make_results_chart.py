"""Render the headline SWE-bench result as a publication-ready chart.

Produces eval/runs/<run-id>/results.png with:
  - left panel: stacked bar of resolved/unresolved/error per agent
  - right panel: Venn-style bucket breakdown (both / only-A / only-B / neither)
"""
import json
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from pathlib import Path

RUN_ID = "verified-gemini-full"
REPO = Path(r"C:\Users\fmche\PycharmProjects\clawcodex")
EVAL_DIR = REPO / "eval" / "runs" / RUN_ID
SWEBENCH = REPO / "SWE-bench-dev"
OUT = EVAL_DIR / "results.png"
# Also write to a tracked path under assets/ so README can reference it
README_OUT = REPO / "assets" / "swebench-verified-gemini.png"

# Load summaries
def load(agent: str) -> dict:
    return json.loads((SWEBENCH / f"{agent}-local.{RUN_ID}-{agent}.json").read_text(encoding="utf-8"))

cc = load("clawcodex")
oc = load("openclaude")
total = max(cc["total_instances"], oc["total_instances"])

# Buckets
cc_resolved = set(cc["resolved_ids"])
oc_resolved = set(oc["resolved_ids"])
both = cc_resolved & oc_resolved
only_cc = cc_resolved - oc_resolved
only_oc = oc_resolved - cc_resolved
neither_count = total - len(both) - len(only_cc) - len(only_oc)

# Colors — clawcodex brand-ish + openclaude muted
COL_CC = "#2563eb"      # blue
COL_OC = "#94a3b8"      # slate
COL_UNRES = "#fbbf24"   # amber
COL_ERR = "#ef4444"     # red

fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6), gridspec_kw={"width_ratios": [1.2, 1]})

# --------- Left: stacked bar of outcomes ---------
agents = ["clawcodex", "openclaude"]
resolved = [cc["resolved_instances"], oc["resolved_instances"]]
unresolved = [cc["unresolved_instances"], oc["unresolved_instances"]]
errors = [cc["error_instances"], oc["error_instances"]]

x = np.arange(len(agents))
w = 0.55
ax1.bar(x, resolved, w, label="Resolved", color=COL_CC, edgecolor="white", linewidth=2)
ax1.bar(x, unresolved, w, bottom=resolved, label="Unresolved", color=COL_UNRES, edgecolor="white", linewidth=2)
ax1.bar(x, errors, w, bottom=[r+u for r, u in zip(resolved, unresolved)], label="Error", color=COL_ERR, edgecolor="white", linewidth=2)

# Annotate resolved values prominently
for i, (agent, n) in enumerate(zip(agents, resolved)):
    pct = 100.0 * n / total
    ax1.text(i, n / 2, f"{n}\n({pct:.1f}%)", ha="center", va="center",
             color="white", fontsize=18, fontweight="bold")
    ax1.text(i, n + unresolved[i] / 2, f"{unresolved[i]}", ha="center", va="center",
             color="#7c2d12", fontsize=11)
    ax1.text(i, n + unresolved[i] + errors[i] / 2, f"{errors[i]}", ha="center", va="center",
             color="white", fontsize=11)

ax1.set_xticks(x)
ax1.set_xticklabels(agents, fontsize=13, fontweight="bold")
ax1.set_ylabel(f"Instances (of {total})", fontsize=12)
ax1.set_title("SWE-bench Verified resolve rate\n(Gemini 2.5 Pro, 499 instances)", fontsize=14, fontweight="bold")
ax1.legend(loc="upper right", fontsize=11)
ax1.set_ylim(0, total * 1.05)
ax1.spines["top"].set_visible(False)
ax1.spines["right"].set_visible(False)
ax1.grid(axis="y", alpha=0.3, linestyle="--")

# Delta annotation
delta = resolved[0] - resolved[1]
delta_str = f"clawcodex +{delta} ({100.0 * delta / total:+.1f} pp)" if delta > 0 else f"clawcodex {delta} ({100.0 * delta / total:+.1f} pp)"
ax1.text(0.5, total + total*0.01, delta_str, ha="center", va="bottom", fontsize=12,
         color=(COL_CC if delta > 0 else COL_OC), fontweight="bold",
         transform=ax1.transData)

# --------- Right: per-instance disagreement breakdown ---------
labels = ["Both\nsolved", "Only\nclawcodex", "Only\nopenclaude", "Neither\nsolved"]
counts = [len(both), len(only_cc), len(only_oc), neither_count]
colors = ["#10b981", COL_CC, COL_OC, "#71717a"]

bars = ax2.barh(labels, counts, color=colors, edgecolor="white", linewidth=2)
for bar, count in zip(bars, counts):
    pct = 100.0 * count / total
    ax2.text(bar.get_width() + 3, bar.get_y() + bar.get_height() / 2,
             f"{count} ({pct:.1f}%)", va="center", fontsize=11, fontweight="bold")

ax2.set_xlim(0, max(counts) * 1.20)
ax2.set_xlabel("Instances", fontsize=12)
ax2.set_title("Per-instance disagreement\n(of 499 evaluated)", fontsize=14, fontweight="bold")
ax2.spines["top"].set_visible(False)
ax2.spines["right"].set_visible(False)
ax2.invert_yaxis()
ax2.grid(axis="x", alpha=0.3, linestyle="--")

# Overall figure title
fig.suptitle(
    f"clawcodex vs openclaude on SWE-bench Verified — "
    f"{resolved[0]}/{total} ({100.0*resolved[0]/total:.1f}%) vs "
    f"{resolved[1]}/{total} ({100.0*resolved[1]/total:.1f}%)",
    fontsize=15, y=1.02
)
plt.tight_layout()
plt.savefig(OUT, dpi=150, bbox_inches="tight", facecolor="white")
print(f"wrote {OUT}")
README_OUT.parent.mkdir(parents=True, exist_ok=True)
plt.savefig(README_OUT, dpi=150, bbox_inches="tight", facecolor="white")
print(f"wrote {README_OUT}")
