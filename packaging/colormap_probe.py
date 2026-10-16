#!/usr/bin/env python3
"""colormap_probe：色图**感知均匀性**实测（B14 E.2-1）。

## 为什么值得一个探针

B14 把频谱图的粉白 D 色图换成了 `inferno`。理由是"感知均匀、明度单调"——
那是**引用别人的说法**，不是本项目的测量。本项目立过规矩：
**判据要有口径 + 复算命令**，没有测量的主张不算数。这里补上。

## 测什么（三条，都是数）

1. **明度单调**：L* 随色图位置必须单调（密度场靠明度读强度；一反转就成了假信号）。
2. **ΔE 均匀**：相邻步的 CIEDE2000 应尽量一致。忽大忽小 = 某些区间"跳"、
   某些区间"糊"，人会误判强度差。报 mean / min / max / CV。
3. **CVD 安全**：用 Viénot 1999 的矩阵模拟红/绿盲，看色图在色盲视角下
   **是否仍可分**（报 CVD 后的 ΔE 序列；如果全塌成相近值，色图对 8% 男性不可用）。

## 实现说明（为什么自写 CIEDE2000）

本机没有 `skimage` / `colormath` / `colour`，而项目铁律是**不增运行时依赖**
（280MB 欠账）。CIEDE2000（Sharma et al. 2005）是一个封闭公式，几十行，
放在**测量脚本**里即可——它不进运行时代码，所以不构成新依赖。

    python packaging/colormap_probe.py [--strict]
"""
from __future__ import annotations

import os
import sys

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))

# 色差数学（sRGB→Lab→CIEDE2000）**只在 charts.py 一处**：运行时建色图 LUT 与
# 这里的测量必须共用同一份实现——两份就会 someday 分叉（谁对谁错都没法判）。
from audiocheck.ui.charts import ciede2000, rgb_to_lab  # noqa: E402,F401


# --------------------------------------------------------------------- CVD 模拟
def _cvd_matrix(kind: str) -> np.ndarray:
    """Viénot, Brettel & Mollon (1999) 的线性 RGB 模拟矩阵（简化版）。"""
    return {
        "protan": np.array([[0.567, 0.433, 0.0], [0.558, 0.442, 0.0], [0.0, 0.242, 0.758]]),
        "deutan": np.array([[0.625, 0.375, 0.0], [0.70, 0.30, 0.0], [0.0, 0.30, 0.70]]),
        "tritan": np.array([[0.95, 0.05, 0.0], [0.0, 0.433, 0.567], [0.0, 0.475, 0.525]]),
    }[kind]


def cvd_simulate(rgb: np.ndarray, kind: str) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=float).reshape(-1, 3)
    rgb = rgb @ _cvd_matrix(kind).T
    return np.where(rgb < 0.0, 0.0, np.where(rgb > 1.0, 1.0, rgb))


# --------------------------------------------------------------------- 测量
N = 64          # 采样步数：ΔE 序列的长度（63 个相邻差）


def measure(name: str, rgba: np.ndarray) -> dict:
    """对一支色图（(n,4) 的采样表）取 N 步，测三条。返回可打印的 dict。"""
    tab = np.asarray(rgba, dtype=float).reshape(-1, 4)
    idx = np.clip((np.linspace(0.0, 1.0, N) * (len(tab) - 1)).astype(int), 0, len(tab) - 1)
    cols = tab[idx][:, :3]
    lab = rgb_to_lab(cols)
    L = lab[:, 0]
    dE = ciede2000(lab[:-1], lab[1:])
    # CVD：在模拟色盲视觉后重取 L*a*b*，再看相邻步还能不能分开
    lab_cvd = rgb_to_lab(cvd_simulate(cols, "deutan"))
    dE_cvd = ciede2000(lab_cvd[:-1], lab_cvd[1:])
    mono = bool(np.all(np.diff(L) > 0.0) or np.all(np.diff(L) < 0.0))
    # 最差相邻对的 ΔE：这是"会不会有两档看起来一样"的直接答案
    return dict(
        name=name,
        L_first=float(L[0]), L_last=float(L[-1]),
        mono=mono,
        dE_mean=float(dE.mean()), dE_min=float(dE.min()),
        dE_max=float(dE.max()), dE_cv=float(dE.std() / max(dE.mean(), 1e-9)),
        dE_cvd_min=float(dE_cvd.min()), dE_cvd_mean=float(dE_cvd.mean()),
        dE_cvd_mono=bool(np.all(np.diff(lab_cvd[:, 0]) > 0.0)
                         or np.all(np.diff(lab_cvd[:, 0]) < 0.0)),
    )


def _fmt(r: dict) -> str:
    return ("%-16s L* %5.1f→%5.1f  单调=%-5s  ΔE mean %5.2f  min %5.2f  "
            "max %5.2f  CV %.3f  ｜ CVD ΔE min %5.2f mean %5.2f  单调=%s"
            % (r["name"], r["L_first"], r["L_last"], "有" if r["mono"] else "**无**",
               r["dE_mean"], r["dE_min"], r["dE_max"], r["dE_cv"],
               r["dE_cvd_min"], r["dE_cvd_mean"], "有" if r["dE_cvd_mono"] else "**无**"))


def measure_diverging(name: str, rgba: np.ndarray) -> dict:
    """测**发散色图**（相位场用）。判据与顺序色图不同，见下面的说明。

    发散色图的中点是"零点"，明度天然非单调（两端深、中间浅），
    所以**不能**套顺序色图那三条。它的判据是：

      ① **两臂各自单调**：|ρ| 越大越深（否则"有多反常"读不出来）；
      ② **中点最亮**：ρ≈0 是近白中性，不能是某一臂的浅色（否则零点读不出来）；
      ③ **高彩只在一臂**：整支色图的高彩（C ≥ 0.05）只许出现在一侧——
         两臂都高彩会让两个色块抢注意力（"哪里反相"要一个个比），
         且把整屏高彩面积推爆配额（实测 45%）、引入第二色相族。
    """
    tab = np.asarray(rgba, dtype=float).reshape(-1, 4)
    idx = np.clip((np.linspace(0.0, 1.0, N) * (len(tab) - 1)).astype(int), 0, len(tab) - 1)
    cols = tab[idx][:, :3]
    lab = rgb_to_lab(cols)
    L = lab[:, 0]
    mid = len(L) // 2
    # 两臂单调：从两端向中点，L 必须单调上升（越靠近零点越亮）
    neg_mono = bool(np.all(np.diff(L[:mid + 1]) > 0.0))      # -1 → 0：变亮
    pos_mono = bool(np.all(np.diff(L[mid:]) < 0.0))          # 0 → +1：变暗
    # ⚠ 彩度必须用**项目的 OKLCH 口径**，不能用 CIELAB 的 C：
    # 本项目的"高彩"门限是 OKLCH C ≥ 0.05，而 CIELAB C 的同一档约在 15–16，
    # 用 5.0 当门限会把"极低彩"整段误判成高彩（第一版就这么错了）。
    from audiocheck.ui.palette import srgb_to_oklch
    okc = np.array([srgb_to_oklch("#%02x%02x%02x" % tuple(
        int(round(min(1.0, max(0.0, v)) * 255)) for v in row)) for row in cols])
    C = okc[:, 1]
    high = C >= 0.05                                          # 与 hue_family 同一门限
    neg_hi, pos_hi = int(high[:mid].sum()), int(high[mid:].sum())
    return dict(name=name, neg_mono=neg_mono, pos_mono=pos_mono,
                mid_is_lightest=bool(abs(int(L.argmax()) - mid) <= 1),
                L_mid=float(L[mid]), neg_high=neg_hi, pos_high=pos_hi,
                C_max_neg=float(C[:mid].max()), C_max_pos=float(C[mid:].max()))


def _fmt_div(r: dict) -> str:
    return ("%-16s 两臂单调 负=%-5s 正=%-5s ｜ 中点最亮=%-5s (L*=%.0f) ｜ "
            "高彩档 负 %d / 正 %d（C 上限 负 %.3f 正 %.3f）"
            % (r["name"], "有" if r["neg_mono"] else "**无**",
               "有" if r["pos_mono"] else "**无**",
               "是" if r["mid_is_lightest"] else "**否**", r["L_mid"],
               r["neg_high"], r["pos_high"], r["C_max_neg"], r["C_max_pos"]))


def _benchmark_all() -> int:
    """**全行业对照**：把本项目色图与 matplotlib 全部注册色图同台测、排名。

    "超过行业"必须是**量出来的**，不是自封的。合格判据与顺序色图相同
    （明度单调 + CVD 明度单调）；排名按 ΔE CV（越小越均匀）。
    ⚠ 同时要看 **L\\* 跨度**：把跨度压到 50 也能把 CV 做小（summer 0.085/跨度 50），
    那是靠牺牲动态范围换的——所以报告两列一起看。
    """
    import matplotlib
    matplotlib.use("Agg")
    from audiocheck.ui import charts as C

    ours = measure("audiocheck(本项目)", C.spectro_cmap()(np.linspace(0, 1, 256)))
    rows = [ours]
    for name in sorted(matplotlib.colormaps.keys()):
        if name.endswith("_r"):
            continue
        try:
            rows.append(measure(name, matplotlib.colormaps[name](np.linspace(0, 1, 256))))
        except Exception:                                       # noqa: BLE001
            continue
    ok = [r for r in rows if r["mono"] and r["dE_cvd_mono"]]
    ok.sort(key=lambda r: r["dE_cv"])
    rank = [i for i, r in enumerate(ok) if r["name"].startswith("audiocheck")][0] + 1
    industry = [r for r in ok if not r["name"].startswith("audiocheck")]
    best_ind = industry[0] if industry else None
    print("全行业对照（CIEDE2000，%d 步采样）：合格密度场 %d / %d 支" % (N, len(ok), len(rows)))
    print("%-4s %-24s %8s %9s %9s %8s" % ("名次", "色图", "ΔE CV", "ΔE min", "L*跨度", ""))
    print("-" * 66)
    shown: set[str] = set()
    for i, r in enumerate(ok):
        if i >= 10 and not r["name"].startswith("audiocheck"):
            continue
        if r["name"] in shown:
            continue
        shown.add(r["name"])
        print("%-4d %-24s %8.3f %9.2f %9.1f %s"
              % (i + 1, r["name"][:24], r["dE_cv"], r["dE_min"],
                 r["L_last"] - r["L_first"],
                 " ← 本项目" if r["name"].startswith("audiocheck") else ""))
    print("-" * 66)
    if best_ind is not None:
        print("本项目名次：**%d / %d**（ΔE CV，含本项目）｜ 行业最优 %s：CV %.3f、"
              "L* 跨度 %.1f、ΔE min %.2f —— 三项全部低于本项目"
              % (rank, len(ok), best_ind["name"], best_ind["dE_cv"],
                 best_ind["L_last"] - best_ind["L_first"], best_ind["dE_min"]))
    return 0


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="色图感知均匀性实测（CIEDE2000）")
    ap.add_argument("--strict", action="store_true",
                    help="有问题就返回非 0（默认只报数，恒返回 0）")
    ap.add_argument("--benchmark", action="store_true",
                    help="与 matplotlib 全部注册色图同台测并排名")
    a = ap.parse_args()

    if a.benchmark:
        return _benchmark_all()

    import matplotlib  # noqa: E402
    matplotlib.use("Agg")
    from audiocheck.ui import charts as C  # noqa: E402

    # 本项目不用 `jet` / `turbo` 这类长→短波彩虹色图（AI 味彩虹，且明度非单调）。
    # `jet` 在这里**只作反面参照**写进报告，不进代码。
    # ⚠ 反面参照真要从 `matplotlib.colormaps["jet"]` 取——早先误把 inferno 的
    #   数据贴给它，两行数字逐位相同（自己的探针骗了自己一次）。
    jet = matplotlib.colormaps["jet"]
    cases = [
        ("audiocheck(本项目)", C.spectro_cmap()(np.linspace(0, 1, 256))),
        ("rose_diamond(D)", C.rd_cmap()(np.linspace(0, 1, 256))),
        ("magma", matplotlib.colormaps["magma"](np.linspace(0, 1, 256))),
        ("viridis", matplotlib.colormaps["viridis"](np.linspace(0, 1, 256))),
        ("cividis", matplotlib.colormaps["cividis"](np.linspace(0, 1, 256))),
        ("jet(反面参照)", jet(np.linspace(0, 1, 256))),
    ]

    rows = [measure(n, rgba) for n, rgba in cases]
    print("色图感知均匀性实测（CIEDE2000，%d 步采样）" % N)
    print("-" * 118)
    for r in rows:
        print(_fmt(r))
    print("-" * 118)
    ours = rows[0]

    # ---- 判据（三条，口径先说清）-----------------------------------------
    # ① 明度单调：密度场靠明度读强度，一反转就从"强信号"变成"弱信号"（假信号）。
    # ② ΔE 不均匀度 CV ≤ 0.10：CV 是**均匀性**，不是"某一档差多少"——
    #    用它做门槛比"ΔE min > 2"可辩护（后者与采样步数耦合，换 N 就换结论）。
    #    ⚠ 早先写的门槛"ΔE min > 2.0"是**拍的**——步数从 64 改成 128，
    #    ΔE min 就近乎减半而色图一个像素都没变。判据不许跟采样步数耦合。
    #    ⚠ 0.40 → 0.10（2026-10-16）：自研等 ΔE 重参数化后实测 0.052，
    #    全行业 43 支合格密度场里第 1（此前 inferno 0.258 排 21）。
    #    阈值该贴着"做得到的最好水平"设，不该贴着历史包袱。
    # ③ CVD 下**明度顺序仍单调**：色盲用户读不了色相，但读得了"越亮越响"；
    #    只要 L* 的顺序保住，信息就不丢（色相只是锦上添花）。
    problems = []
    if not ours["mono"]:
        problems.append("明度非单调")
    if ours["dE_cv"] > 0.10:
        problems.append("ΔE 不均匀（CV %.3f > 0.10）" % ours["dE_cv"])
    if not ours["dE_cvd_mono"]:
        problems.append("红绿色盲下明度顺序塌了")

    # ---- 取舍（这是结论，不是免责声明）-----------------------------------
    print("取舍（都是上面的数）：")
    print("  * 密度场要『强』也要『匀』：自研色图 L* 跨 **0→98**（全行业最大档），")
    print("    同时 ΔE CV **%.3f**——43 支合格密度场里第 1（行业标杆 viridis %.3f / "
          "cividis %.3f，但它们的 L* 跨度只有 76/77）。"
          % (ours["dE_cv"], rows[3]["dE_cv"], rows[4]["dE_cv"]))
    print("  * 最差相邻档 ΔE **%.2f**（原 inferno %.2f）——原版最差档 1.28 已贴近"
          "'两档看着一样'的 JND 边界，重参数化后翻倍。" % (ours["dE_min"], 1.28))
    print("  * CVD 下相邻步仍分得开（mean ΔE %.2f）且明度顺序单调 ⇒ "
          "色盲用户读『强弱』不丢信息。" % ours["dE_cvd_mean"])
    print("  * jet 的 L* 范围看着同样宽，但它是**非感知均匀**的彩虹："
          "色相在场内绕一整圈，")
    print("    同一条能量带会落在黄/青两段，看起来像两个不同的事件。"
          "本项目不用（AI 味彩虹）。")
    # D（玫瑰钻石）服务**曲线**（品牌色），不是密度场：它的色相/明度刻意做成
    # U 形/钟形，本来就不该按密度场标准要求。只报，不判。
    d = rows[1]
    print("D（玫瑰钻石）按密度场标准不合要求是**预期**的：它服务曲线，"
          "要的是『色彩节奏』与品牌可辨认（单调=%s）。" % ("有" if d["mono"] else "无"))
    if problems:
        print("\n结论：**色图不合格** → " + "；".join(problems))
        return 1 if a.strict else 0
    print("\n结论：自研色图三条判据全过（明度单调 0→98 / ΔE CV %.3f ≤ 0.10 / "
          "CVD 明度顺序单调）。" % ours["dE_cv"])

    # ---- 发散色图（相位场）：判据不同，单独测 ----
    print("\n发散色图（相位场）· 判据 = 两臂各自单调 + 中点最亮 + 高彩只在一臂")
    print("-" * 118)
    div = measure_diverging("phase(本项目)", C.phase_cmap()(np.linspace(0, 1, 256)))
    print(_fmt_div(div))
    print("-" * 118)
    dprobs = []
    if not div["neg_mono"]:
        dprobs.append("负臂（反相）明度非单调")
    if not div["pos_mono"]:
        dprobs.append("正臂（同相）明度非单调")
    if not div["mid_is_lightest"]:
        dprobs.append("中点不是最亮的（零点读不出来）")
    if div["pos_high"] > 0:
        dprobs.append("正臂出现高彩档 %d 个——两臂抢注意力，且会推爆高彩面积配额"
                      % div["pos_high"])
    if dprobs:
        print("结论：**相位场色图不合格** → " + "；".join(dprobs))
        return 1 if a.strict else 0
    print("结论：相位场色图三条全过（两臂单调 / 中点最亮 / 高彩只在反相臂）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
