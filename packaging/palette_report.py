#!/usr/bin/env python3
"""配色对照度报告 + 色卡图。

三件事：

1. **审计两个内置主题**（DARK / LIGHT）——它们由 `palette.build()` 生成，
   但审计照做：生成器会改、种子会换，改了之后不达标必须在这里看见；
2. 用几个种子色生成配色（深/浅 × 两档风格）并审计，生成时就不达标会直接抛异常；
3. 出一张色卡 PNG（`--out`），肉眼比对用——含装饰色阶那一行 6 个小格。

跑法：

    python packaging/palette_report.py
    python packaging/palette_report.py --seed "#e0a92e" --vibe pop
    python packaging/palette_report.py --out out/palette_swatches.png
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
    n_bad = len([f for f in P.audit(pal) if not f.ok])
    print("  -- 装饰色阶（非文本 3:1）--")
    print("\n".join("  " + f.line() for f in P.deco_checks(pal)))
    n_bad += len([f for f in P.deco_checks(pal) if not f.ok])
    return n_bad


def swatches(cases, out: str) -> None:
    """色卡：每行一套主题，每列一个语义角色 + 装饰色阶 6 小格。"""
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
    deco_n = 6
    fig = Figure(figsize=(0.62 * cols + 0.34 * deco_n + 2.4, 0.62 * rows + 1.1), dpi=110)
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
        # 装饰色阶：一行小格紧跟其后。它和语义色**故意分开画**——
        # 混在一列里会让人以为它们承担同一类信息。
        for i, col in enumerate(pal.get("deco") or ()):
            ax.add_patch(mp.Rectangle((cols + 0.4 + i, y), 0.8, 0.94,
                                      facecolor=col, edgecolor="#8888", linewidth=0.4))
    for c, role in enumerate(ROLES):
        ax.text(c + 0.47, rows + 0.1, role, rotation=90, ha="center", va="bottom",
                fontsize=6.5)
    for i in range(deco_n):
        ax.text(cols + 0.8 + i, rows + 0.1, "d%d" % i, rotation=90, ha="center",
                va="bottom", fontsize=6.5)
    ax.set_xlim(-6.5, cols + 0.4 + deco_n)
    ax.set_ylim(-0.2, rows + 5.2)
    fig.tight_layout()
    fig.savefig(out, facecolor="#ffffff")
    print("\n色卡 → %s" % out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", default=None, help="额外生成一个种子色（#rrggbb）")
    ap.add_argument("--mode", default="both", choices=("dark", "light", "both"))
    ap.add_argument("--vibe", default="both", choices=("calm", "pop", "both"),
                    help="风格档：calm=近中性表面 ｜ pop=带色表面+高彩度装饰阶")
    ap.add_argument("--out", default=os.path.join(_ROOT, "out", "palette_swatches.png"))
    args = ap.parse_args(argv)

    print("### 1. 内置主题的对照度审计（不粉饰：不达标就是列出来）")
    print("（这两个主题由 palette.build(seed, mode, 'pop') 生成，但审计照做——")
    print("  生成器改了、种子换了，改了之后不达标必须在这里看见）")
    bad = 0
    bad += audit_print(T.DARK.p, "DARK")
    bad += audit_print(T.LIGHT.p, "LIGHT")

    print("\n\n### 2. 由种子色生成的配色（生成时 strict 自审，不达标会抛异常）")
    seeds = list(SEEDS)
    if args.seed:
        seeds.insert(0, (args.seed, "自定义"))
    modes = ("dark", "light") if args.mode == "both" else (args.mode,)
    vibes = ("calm", "pop") if args.vibe == "both" else (args.vibe,)
    cases = []
    for mode in modes:
        for vibe in vibes:
            for seed, label in seeds:
                pal = P.build(seed, mode, vibe)
                misses = audit_print(pal, "%s ｜ %s ｜ 种子 %s %s"
                                     % (mode.upper(), vibe, seed, label))
                bad += misses
                cases.append(("%s %s %s" % (mode, vibe, label), pal))

    print("\n\n### 3. 汇总")
    print("生成配色不达标项：%d（`build()` 内部就是 strict 自审，不达标会直接抛异常）"
          % bad)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    swatches(cases, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
