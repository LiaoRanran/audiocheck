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


# ------------------------------------------------------------------ 面积配额

#: "高彩面积"的判据：OKLCH 彩度超过这个值就算高彩。
#: 为什么是 OKLCH 而不是 HSL/HSV：**同一个"高彩"阈值在三个空间里差好几倍**，
#: 而本轮引用的那一族数字（配额 4.59% 之类）只说了值、没说空间。实测同一张
#: 界面截图：OKLCH C>0.10 是 3.3%，而 HSV S>0.35 是 4.7%、HSL S>0.35 是 21.9%
#: ——最大差 6 倍。所以在报告里**两个空间都算**，免得下次又拿错口径比数字。
HIGH_CHROMA_C = 0.10
#: HSL/HSV 口径的对照阈值（历史数字用的就是这一族）。
HIGH_CHROMA_S = 0.35


def _oklch_chroma_and_sat(path: str):
    """读一张图，返回 (OKLCH 彩度数组, HSV 饱和度数组, 总像素数)。

    用 numpy 向量化：1362×900 的截图逐像素算要十几秒，向量化后 ~0.2 s。
    """
    import numpy as np
    from PIL import Image

    rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0
    flat = rgb.reshape(-1, 3)
    lin = np.where(flat <= 0.04045, flat / 12.92, ((flat + 0.055) / 1.055) ** 2.4)
    lms = lin @ np.array([
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005]]).T
    lab = np.cbrt(lms) @ np.array([
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660]]).T
    chroma = np.hypot(lab[:, 1], lab[:, 2])
    mx, mn = flat.max(axis=1), flat.min(axis=1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-9), 0.0)
    return chroma, sat, int(flat.shape[0])


def area_quota(path: str) -> int:
    """量一张**渲染截图**里的高彩面积占比，与 ≤5% 的配额比对。

    这是"配额制配色"唯一无法靠读令牌表验证的一条：令牌只能说明
    "有哪些颜色"，面积要**渲染出来量**才知道（18 张卡各带一条 2px 色条、
    侧边栏 9 个彩色图标，加起来是多少，光看代码估不准）。

    返回不达标条数（供 `--strict` 用）。
    """
    import numpy as np

    chroma, sat, n = _oklch_chroma_and_sat(path)
    hi_c = float((chroma > HIGH_CHROMA_C).mean() * 100)
    hi_s = float((sat > HIGH_CHROMA_S).mean() * 100)
    neutral = float((chroma <= P.NEUTRAL_CHROMA_MAX).mean() * 100)
    q50, q95 = (float(np.percentile(chroma, 50) * 1000),
                float(np.percentile(chroma, 95) * 1000))
    print("\n== 面积配额：%s（%.0f 万像素）==" % (os.path.basename(path), n / 10000.0))
    print("  高彩面积  OKLCH C>%.2f → **%.2f%%**（配额 ≤5%%）"
          " ｜ HSV S>%.2f → %.2f%%（对照口径）"
          % (HIGH_CHROMA_C, hi_c, HIGH_CHROMA_S, hi_s))
    print("  中性面积  OKLCH C≤%.4f → %.2f%%（配额制要的是「绝大多数」）"
          % (P.NEUTRAL_CHROMA_MAX, neutral))
    print("  彩度分位  中位 %.1f / 95%% %.1f（×1000，便于看量级）" % (q50, q95))

    # pop 档豁免中性面积检查：它的**设计目的**就是"表面带色"（vibe 的定义见
    # `palette.VIBE_TINT`），拿仪器灰的配额去卡它等于右脑判左脑。
    # 判据按文件名识别而不是加参数：调用方要显式把图命名成 pop_*.png，
    # 这个"显式"本身就是登记——免检是有意为之，不是漏检。
    exempt = "pop" in os.path.basename(path).lower()
    bad = 0
    if hi_c > 5.0:
        print("  ✗ 高彩面积 %.2f%% 超出 5%% 配额" % hi_c)
        bad += 1
    if neutral < 80.0:
        if exempt:
            print("  ~ 中性面积 %.1f%%：pop 档**豁免**此条（表面带色是它的定义）" % neutral)
        else:
            print("  ✗ 中性面积只有 %.1f%%——「90%% 中性 + 一枚 accent」没做到" % neutral)
            bad += 1
    if not bad:
        print("  ✓ 配额通过")
    return bad


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
    ap.add_argument("--shot", action="append", default=[],
                    help="量一张**渲染截图**的高彩面积配额（可给多次）")
    ap.add_argument("--strict", action="store_true", help="配额不达标时退出码 1")
    args = ap.parse_args(argv)

    print("### 1. 内置主题的对照度审计（不粉饰：不达标就是列出来）")
    print("（这两个主题由 palette.build(%r, mode, %r) 生成，但审计照做——"
          % (T.BUILTIN_SEED, T.BUILTIN_VIBE))
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

    print("\n\n### 3. 面积配额（**必须量渲染结果**：令牌只能说明「有哪些颜色」）")
    quota_bad = 0
    for shot in args.shot:
        if not os.path.exists(shot):
            print("  跳过（文件不存在）：%s" % shot)
            continue
        quota_bad += area_quota(shot)
    if not args.shot:
        print("  （未给 --shot：跑一下 `python packaging/palette_report.py "
              "--shot shots/x.png` 量真实界面）")

    print("\n\n### 4. 汇总")
    print("生成配色不达标项：%d（`build()` 内部就是 strict 自审，不达标会直接抛异常）"
          % bad)
    print("面积配额不达标项：%d" % quota_bad)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    swatches(cases, args.out)
    return 1 if (args.strict and (bad or quota_bad)) else 0


if __name__ == "__main__":
    sys.exit(main())
