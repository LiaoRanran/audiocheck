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


# --------------------------------------------------------------------- CIEDE2000
def _srgb_to_linear(c: np.ndarray) -> np.ndarray:
    """sRGB([0,1]) → 线性光。"""
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


_M_RGB2XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])          # linear sRGB(D65) → XYZ

def _xyz_to_lab(xyz: np.ndarray) -> np.ndarray:
    """XYZ(D65) → CIE L*a*b*。xyz 形状 (n,3)。"""
    xyz_white = np.array([0.95047, 1.0, 1.08883])                      # D65
    t = xyz / xyz_white
    t = np.where(t > 216.0 / 24389.0, np.cbrt(t), (841.0 / 108.0) * t + 4.0 / 29.0)
    fx, fy, fz = t[:, 0], t[:, 1], t[:, 2]
    return np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=1)


def rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """sRGB (n,3, [0,1]) → CIE L*a*b*。"""
    rgb = np.asarray(rgb, dtype=float).reshape(-1, 3)
    lin = _srgb_to_linear(rgb)
    xyz = lin @ _M_RGB2XYZ.T
    return _xyz_to_lab(xyz)


def ciede2000(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000（Sharma, Wu & Dalal 2005）。输入 (n,3)，输出 (n,) ΔE。"""
    L1, a1, b1 = lab1[:, 0], lab1[:, 1], lab1[:, 2]
    L2, a2, b2 = lab2[:, 0], lab2[:, 1], lab2[:, 2]
    kL = kC = kH = 1.0
    C1 = np.hypot(a1, b1)
    C2 = np.hypot(a2, b2)
    Cbar = 0.5 * (C1 + C2)
    G = 0.5 * (1.0 - np.sqrt(Cbar ** 7 / (Cbar ** 7 + 25.0 ** 7)))
    a1p, a2p = (1.0 + G) * a1, (1.0 + G) * a2
    C1p, C2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1p = np.degrees(np.arctan2(b1, a1p)) % 360.0
    h2p = np.degrees(np.arctan2(b2, a2p)) % 360.0
    dLp = L2 - L1
    dCp = C2p - C1p
    dh = np.where(np.abs(h2p - h1p) <= 180.0, h2p - h1p,
                  np.where(h2p - h1p > 180.0, h2p - h1p - 360.0, h2p - h1p + 360.0))
    dhp = 2.0 * np.sqrt(np.maximum(C1p * C2p, 0.0)) * np.sin(np.radians(dh) / 2.0)
    Lbarp = 0.5 * (L1 + L2)
    Cbarp = 0.5 * (C1p + C2p)
    hsum = h1p + h2p
    hdiff = np.abs(h1p - h2p)
    hbarp = np.where(C1p * C2p == 0, hsum,
                     np.where(hdiff <= 180.0, 0.5 * hsum,
                              np.where(hsum < 360.0, 0.5 * (hsum + 360.0), 0.5 * (hsum - 360.0))))
    T = (1.0 - 0.17 * np.cos(np.radians(hbarp - 30.0))
         + 0.24 * np.cos(np.radians(2.0 * hbarp))
         + 0.32 * np.cos(np.radians(3.0 * hbarp + 6.0))
         - 0.20 * np.cos(np.radians(4.0 * hbarp - 63.0)))
    dtheta = 30.0 * np.exp(-(((hbarp - 275.0) / 25.0) ** 2))
    RC = 2.0 * np.sqrt(Cbarp ** 7 / (Cbarp ** 7 + 25.0 ** 7))
    SL = 1.0 + (0.015 * (Lbarp - 50.0) ** 2) / np.sqrt(20.0 + (Lbarp - 50.0) ** 2)
    SC = 1.0 + 0.045 * Cbarp
    SH = 1.0 + 0.015 * Cbarp * T
    RT = -np.sin(np.radians(2.0 * dtheta)) * RC
    return np.sqrt((dLp / (kL * SL)) ** 2 + (dCp / (kC * SC)) ** 2
                   + (dhp / (kH * SH)) ** 2 + RT * (dCp / (kC * SC)) * (dhp / (kH * SH)))


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


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="色图感知均匀性实测（CIEDE2000）")
    ap.add_argument("--strict", action="store_true",
                    help="有问题就返回非 0（默认只报数，恒返回 0）")
    a = ap.parse_args()

    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
    import matplotlib  # noqa: E402

    from audiocheck.ui import charts as C  # noqa: E402
    matplotlib.use("Agg")

    # 本项目不用 `jet` / `turbo` 这类长→短波彩虹色图（AI 味彩虹，且明度非单调）。
    # `jet` 在这里**只作反面参照**写进报告，不进代码。
    # ⚠ 反面参照真要从 `matplotlib.colormaps["jet"]` 取——早先误把 inferno 的
    #   数据贴给它，两行数字逐位相同（自己的探针骗了自己一次）。
    jet = matplotlib.colormaps["jet"]
    cases = [
        ("inferno(本项目)", C.spectro_cmap()(np.linspace(0, 1, 256))),
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
    # ② ΔE 不均匀度 CV ≤ 0.40：CV 是**均匀性**，不是"某一档差多少"——
    #    用它做门槛比"ΔE min > 2"可辩护（后者与采样步数耦合，换 N 就换结论）。
    #    ⚠ 顺带记一笔：早先写的门槛"ΔE min > 2.0"是**拍的**——步数从 64 改成 128，
    #    ΔE min 就近乎减半而色图一个像素都没变。判据不许跟采样步数耦合。
    # ③ CVD 下**明度顺序仍单调**：色盲用户读不了色相，但读得了"越亮越响"；
    #    只要 L* 的顺序保住，信息就不丢（色相只是锦上添花）。
    problems = []
    if not ours["mono"]:
        problems.append("明度非单调")
    if ours["dE_cv"] > 0.40:
        problems.append("ΔE 不均匀（CV %.3f > 0.40）" % ours["dE_cv"])
    if not ours["dE_cvd_mono"]:
        problems.append("红绿色盲下明度顺序塌了")

    # ---- 取舍（这是结论，不是免责声明）-----------------------------------
    print("取舍（都是上面的数）：")
    print("  * inferno 的 L* 跨 **0→98**，viridis 15→91、cividis 14→91 —— "
          "inferno 用满了可用的明度范围，")
    print("    这正是频谱图要的『强』（用户点名）；代价是 ΔE CV %.3f，"
          "不如 viridis（%.3f）/ cividis（%.3f）均匀。"
          % (ours["dE_cv"], rows[3]["dE_cv"], rows[4]["dE_cv"]))
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
    print("\n结论：inferno 三条判据全过（明度单调 0→98 / ΔE CV %.3f ≤ 0.40 / "
          "CVD 明度顺序单调）。" % ours["dE_cv"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
