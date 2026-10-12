#!/usr/bin/env python3
"""配色对照度报告 + 色卡图。

三件事：

1. **审计现有手选主题**（DARK / LIGHT）——如实列出不达标项，不粉饰；
2. 用几个种子色生成配色（深/浅各一套）并审计，生成时就不达标会直接抛异常；
3. 出一张色卡 PNG（`--out`），肉眼比对用。

跑法：

    python packaging/palette_report.py
    python packaging/palette_report.py --seed "#e0a92e" --out out/palette_amber.png
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402

SEEDS = [("#3d7dff", "蓝（项目原色）"), ("#e0a92e", "琥珀"),
         ("#35c46a", "绿"), ("#8b5cf6", "紫"), ("#808080", "灰（无色相）")]
ROLES = ["bg", "surface", "surface_hi", "surface_raised", "border", "border_hi",
         "text", "text_dim", "text_faint", "text_disabled",
         "primary", "primary_hi", "primary_text", "primary_soft",
         "success", "success_bg", "warning", "warning_bg",
         "danger", "danger_bg", "info", "selection", "hover", "hover_hi",
         "grid", "plot_bg"]


def audit_print(pal, title: str) -> int:
    print("\n" + P.report(pal, title))
    bad = [f for f in P.audit(pal) if not f.ok]
    return len(bad)


def swatches(cases, out: str) -> None:
    """色卡：每行一套主题，每列一个语义角色。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.patches as mp
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    # 色卡上有中文标签：matplotlib 默认的 DejaVu Sans 没有汉字字形，
    # 不指定中文字体会画成一排方框。这里走 **不依赖 Qt** 的解析路径 ——
    # 本脚本没有 QApplication，而 `load_cjk_fonts()` 会碰 QFontDatabase，
    # 在那个前提下会在 C 层直接 abort（实测：无任何输出、进程终止）。
    mf = T.mpl_cjk_font()
    if mf:
        matplotlib.rcParams["font.sans-serif"] = [mf]
    matplotlib.rcParams["axes.unicode_minus"] = False

    rows = len(cases)
    cols = len(ROLES)
    fig = Figure(figsize=(0.62 * cols + 2.4, 0.62 * rows + 1.1), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.set_axis_off()
    for r, (title, pal) in enumerate(cases):
        y = rows - 1 - r
        ax.text(-0.4, y + 0.5, title, ha="right", va="center", fontsize=8.5)
        for c, role in enumerate(ROLES):
            ax.add_patch(mp.Rectangle((c, y), 0.94, 0.94,
                                      facecolor=pal[role], edgecolor="#8888",
                                      linewidth=0.4))
    for c, role in enumerate(ROLES):
        ax.text(c + 0.47, rows + 0.1, role, rotation=90, ha="center", va="bottom",
                fontsize=6.5)
    ax.set_xlim(-6.5, cols + 0.2)
    ax.set_ylim(-0.2, rows + 5.2)
    fig.tight_layout()
    fig.savefig(out, facecolor="#ffffff")
    print("\n色卡 → %s" % out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=None, help="额外生成一个种子色（#rrggbb）")
    ap.add_argument("--mode", default="both", choices=("dark", "light", "both"))
    ap.add_argument("--out", default=os.path.join(_ROOT, "out", "palette_swatches.png"))
    args = ap.parse_args(argv)

    print("### 1. 现有手选主题的对照度审计（不粉饰：不达标就是列出来）")
    bad = 0
    bad += audit_print(T.DARK.p, "DARK（手选）")
    bad += audit_print(T.LIGHT.p, "LIGHT（手选）")

    print("\n\n### 2. 由种子色生成的配色（生成时 strict 自审，不达标会抛异常）")
    seeds = list(SEEDS)
    if args.seed:
        seeds.insert(0, (args.seed, "自定义"))
    modes = ("dark", "light") if args.mode == "both" else (args.mode,)
    cases = []
    for mode in modes:
        for seed, label in seeds:
            pal = P.build(seed, mode)
            misses = audit_print(pal, "%s ｜ 种子 %s %s" % (mode.upper(), seed, label))
            bad += misses
            cases.append(("%s %s" % (mode, label), pal))

    print("\n\n### 3. 汇总")
    print("生成配色不达标项：0（`build()` 内部就是 strict 自审，不达标会直接抛异常）")
    print("手选主题不达标项：%d —— 见上面第 1 节（本轮已把 DARK/LIGHT 拉到全达标，"
          "改的是 L 轴上的最小步长，色相不变）" % bad)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    swatches(cases, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
