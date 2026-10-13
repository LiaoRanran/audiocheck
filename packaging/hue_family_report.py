#!/usr/bin/env python3
"""高彩面积 + 色相族报告（B7 的判据 C5 复算脚本）。

## 为什么单开一个脚本

`palette_report.py` 管的是**令牌层**（角色色值之间的对比度），而这一份管的是
**渲染结果层**——高彩像素占了多少面积、集中在几个色相上。两者不能合并：

* 令牌能说明"有哪些颜色"，说明不了"这些颜色铺了多大、几个色相"；
* 三条 2px 的细线在令牌层完全合法（面积判据抓不到），却能把"一枚 accent"
  稀释成六个色相。这正是"面积配额"的盲区——**配额约束面积，不约束色相数量**。

## 口径（写死，不许换）

* 色彩空间：**OKLCH**（Ottosson 公式，内联，不依赖第三方色彩库）。
  ⚠ 同一张图在 OKLCH / HSL / HSV 三个空间里的"高彩面积"能差 **6 倍**
  （实测：OKLCH C>0.10 是 0.68%，HSV S>0.35 是 1.83%，HSL S>0.35 是 21.9%）。
  引用数字时必须连空间一起说，否则两边永远对不上。
* 高彩阈值：`C > 0.10`。取锚点图高彩的 P50（0.123）略下方。
* 色相族：**30° 桶**，判据是"单一桶占比 ≥ 85%"。
* 采样：默认逐像素（步长 1）。锚点图 229 万像素约 0.5 s，不必抽稀。

## 面积不是常量（这条必须写在报告里）

同一份设计测出的高彩面积**依赖状态**：哪个视图、装了几个文件、当前判定是
哪一档。所以面积只能作为"同一场景下改前 vs 改后"的对照，**不能当绝对值判据**。
色相族占比则稳定得多（它是比例，与状态弱相关），所以它是主判据。

跑法：

    python packaging/hue_family_report.py shots/b7/dark.png shots/b7/light.png
    python packaging/hue_family_report.py --anchor     # 顺便打印锚点图的参考值
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from PIL import Image

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

#: 高彩阈值与族判据取自 `palette`——**唯一真源**。
#: 脚本里再写一份 0.10 的后果是：哪天口径改了，量尺和测试会给出两个结论，
#: 而两边都"有据可查"。这类分叉在本项目里出现过（两张时长令牌表）。
from audiocheck.ui.palette import HIGH_CHROMA_C as HIGH_C  # noqa: E402
from audiocheck.ui.palette import HUE_FAMILY_MIN as FAMILY_MIN  # noqa: E402

BUCKET_DEG = 30

#: 锚点图的参考值（2026-10-13 实测，复现脚本见本文件与
#: `锚点图拆解_高级的量化定义.md` §5）。**只作对照，不是目标**——
#: 它是 1194×1920 的插画，我们是数据工具，面积不可直接类比。
ANCHOR_REF = {"area": 3.62, "family": 89.3, "p50": 0.123, "p90": 0.167,
              "C<0.05": 86.17, "L>0.7": 84.69}


def oklch(rgb_u8):
    """(N,3) uint8 → (L, C, H)。向量化；纯 Python 逐点要十几秒，这里 ~0.3 s。"""
    a = np.asarray(rgb_u8, dtype=np.float64) / 255.0
    lin = np.where(a <= 0.04045, a / 12.92, ((a + 0.055) / 1.055) ** 2.4)
    lab = np.cbrt(lin @ np.array([
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005]]).T) @ np.array([
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660]]).T
    return lab[:, 0], np.hypot(lab[:, 1], lab[:, 2]), \
        np.degrees(np.arctan2(lab[:, 2], lab[:, 1])) % 360.0


def measure(path: str, rows_excluded=("__none__",)) -> dict:
    """量一张图。`rows_excluded` 是"H 类"区间的名字，见 `--exclude-charts`。"""
    im = Image.open(path).convert("RGB")
    a = np.asarray(im)
    L, C, H = oklch(a.reshape(-1, 3))
    out = {"w": im.width, "h": im.height, "n": int(C.size),
           "area": float((C > HIGH_C).mean() * 100),
           "achromatic": float((C < 0.05).mean() * 100),
           "light": float((L > 0.7).mean() * 100)}
    hi = C > HIGH_C
    out["hi_n"] = int(hi.sum())
    if hi.any():
        b = (H[hi] // BUCKET_DEG * BUCKET_DEG).astype(int)
        vals, cnts = np.unique(b, return_counts=True)
        buckets = {int(k): int(v) for k, v in zip(vals, cnts)}
        out["buckets"] = buckets
        out["family"] = max(buckets.values()) / hi.sum() * 100
        out["family_hue"] = max(buckets, key=buckets.get)
        q = np.percentile(C[hi], [50, 90])
        out["p50"], out["p90"] = float(q[0]), float(q[1])
    return out


def report(path: str, note: str = "") -> int:
    """打印一张图的报告，返回不达标条数。"""
    m = measure(path)
    name = os.path.relpath(path, _ROOT) if path.startswith(_ROOT) else path
    print("\n== %s（%d×%d，%.0f 万像素）%s =="
          % (name, m["w"], m["h"], m["n"] / 10000.0, ("  " + note) if note else ""))
    line = "   高彩面积 C>%.2f   %6.2f%%" % (HIGH_C, m["area"])
    if m.get("hi_n"):
        line += "（%d 点）" % m["hi_n"]
    print(line)
    if not m.get("hi_n"):
        print("   没有高彩像素——accent 缺失（比面积超标更严重）")
        return 1
    print("   极低彩 C<0.05     %6.2f%%   高明度 L>0.7  %6.2f%%"
          % (m["achromatic"], m["light"]))
    print("   高彩彩度 P50/P90   %.3f / %.3f" % (m["p50"], m["p90"]))
    bs = m["buckets"]
    fam = m["family"]
    top = sorted(bs.items(), key=lambda kv: -kv[1])[:5]
    print("   色相桶（30°）      %s"
          % "  ".join("%d°:%.1f%%" % (k, v / m["hi_n"] * 100) for k, v in top))
    bad = 0
    mark = "✓" if fam >= FAMILY_MIN * 100 else "✗"
    print("   单一色相族         %s %5.1f%%（要求 ≥%.0f%%，主族 %d°）"
          % (mark, fam, FAMILY_MIN * 100, m["family_hue"]))
    if fam < FAMILY_MIN * 100:
        bad += 1
        # 把"稀释"这件事指出来：主族之外的桶各占多少
        others = [(k, v / m["hi_n"] * 100) for k, v in bs.items() if k != m["family_hue"]]
        print("      被稀释：其余 %d 个色相合计 %.1f%% —— %s"
              % (len(others), sum(v for _k, v in others),
                 "  ".join("%d°:%.1f%%" % kv for kv in sorted(others, key=lambda kv: -kv[1]))))
    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("shots", nargs="*", help="要量的截图")
    ap.add_argument("--anchor", action="store_true", help="打印锚点图参考值")
    ap.add_argument("--strict", action="store_true", help="色相族不达标时退出码 1")
    args = ap.parse_args(argv)

    if args.anchor:
        print("锚点图参考值（2026-10-13 实测，`锚点图拆解_高级的量化定义.md`）：")
        for k, v in ANCHOR_REF.items():
            print("   %-8s %s" % (k, v))
        print("   ⚠ 它是 1194×1920 的插画、L>0.7 占 84.7%（高调亮图）。")
        print("     面积不可直接类比；**可迁移的是比例关系**（彩度 35 倍、冷暖对立、单族 89%）。")

    if not args.shots:
        print("\n（没给截图。跑一下 `python packaging/hue_family_report.py shots/b7/dark.png`）")
        return 0

    bad = 0
    for s in args.shots:
        if not os.path.exists(s):
            print("\n== %s：文件不存在" % s)
            bad += 1
            continue
        bad += report(s)
    print("\n色相族不达标：%d" % bad)
    return 1 if (args.strict and bad) else 0


if __name__ == "__main__":
    sys.exit(main())
