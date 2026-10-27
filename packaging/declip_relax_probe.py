#!/usr/bin/env python3
"""declip_relax_probe：POCS **超松弛**（over-relaxation）能不能加速？（先量，再定）

## 问题

调研（Agent POCS 题）结论：削波文献**没有对 POCS 交替投影标定 λ**（官方实现统一取
λ=1，"覆盖所有情形"的折中）；凸集交替投影理论允许 λ∈(0,2)。本项目实测（§20⑦）：
128 次迭代时 ΔSDR **仍在大幅改善**（64→128 涨 14.2 dB）⇒ 收敛极慢，加速有真实价值。

超松弛的形态（对**迭代末端的整体更新**做外推）：

    x_{k+1} = x_k + λ·(P(x_k) − x_k)，  P = 削波不等式投影 ∘ 已知样本写回 ∘ 带限投影

λ=1 即现行 POCS。λ>1 外推、λ>2 会震荡。

## 量什么

中度削波（放大 2.2× 后 clip 到 ±0.9，560 饱和样本）与稀疏削波两个场景，扫
λ ∈ {0.8, 1.0, 1.2, 1.5, 1.8} × 迭代 ∈ {16, 32, 64, 128}：ΔSDR 与**末步变化量**。

    python packaging/declip_relax_probe.py
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import corpus  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000


def pocs_relaxed(a: np.ndarray, ceiling: float, fs: int, bandwidth: float,
                 iterations: int, lam: float):
    """带超松弛的 POCS（与 `declip.reconstruct` 同构，只多一个 λ 外推）。"""
    a2 = np.asarray(a, dtype=np.float64)
    a2 = a2[:, None] if a2.ndim == 1 else a2      # 已在探针里给成 (n,1) ⇒ 不再加轴
    n, ch = a2.shape
    f = np.fft.rfftfreq(n, 1.0 / fs)
    keep = f <= bandwidth
    sign = np.where(a2 >= 0.0, 1.0, -1.0)
    sat = np.abs(a2) >= ceiling * (1.0 - 1e-12)
    out = a2.copy()
    d_last = 0.0
    for c in range(ch):
        x = a2[:, c].copy()
        k = ~sat[:, c]
        s = sat[:, c]
        sg = sign[:, c]
        d = 0.0
        for _ in range(int(iterations)):
            before = x.copy()
            X = np.fft.rfft(x)
            X[~keep] = 0.0
            x = np.fft.irfft(X, n=n)
            x[k] = a2[k, c]
            x[s] = sg[s] * np.maximum(np.abs(x[s]), ceiling)
            if lam != 1.0:
                x = before + lam * (x - before)          # 超松弛外推
                x[k] = a2[k, c]                          # 已知样本仍**逐位**写回
                x[s] = sg[s] * np.maximum(np.abs(x[s]), ceiling)
            d = float(np.sqrt(np.mean((x - before) ** 2)))
        d_last = max(d_last, d)
        out[:, c] = x
    return out, d_last


def main() -> int:
    scenarios = []
    clean = corpus.harmonic_tone(1.0, FS, amp=0.5, seed=3)
    loud = clean * 2.2
    scenarios.append(("中度削波 (clip ±0.9)", clean, np.clip(loud, -0.9, 0.9), loud, 0.9))
    clean2 = corpus.harmonic_tone(1.0, FS, amp=0.3, seed=5)
    scenarios.append(("稀疏削波 (clip ±0.25)", clean2, np.clip(clean2 * 1.4, -0.25, 0.25),
                      clean2 * 1.4, 0.25))

    for name, _ref, y, truth, ceil in scenarios:
        sat = int((np.abs(y) >= ceil * (1 - 1e-12)).sum())
        print("\n== %s（饱和 %d 样本）==" % (name, sat))
        print("%-6s %-8s %-10s %-10s" % ("λ", "iters", "ΔSDR", "末步 dB"))
        print("-" * 44)
        base = sdr_db(y, truth) or 0.0
        for lam in (0.8, 1.0, 1.2, 1.5, 1.8):
            for iters in (16, 32, 64, 128):
                out, d = pocs_relaxed(y, ceil, FS, 18000.0, iters, lam)
                d_db = 20 * np.log10(d / (float(np.sqrt(np.mean(y ** 2))) or 1.0)) if d > 0 else None
                print("%-6.1f %-8d %+10.2f %-10s"
                      % (lam, iters, (sdr_db(out, truth) or 0) - base,
                         "—" if d_db is None else "%.1f" % d_db))
            print("-" * 44)
    print("""
读法：
* 同一 λ 下 ΔSDR 随迭代单调涨 ⇒ 收敛未到；比较各 λ 在**同迭代数**下的 ΔSDR。
* λ>1 若在同迭代下更高且不发散 ⇒ 超松弛有真实加速；λ>1 若某档掉头/震荡 ⇒ 不稳。
* 只看**定解**场景（中度削波：f≈1.2% << 阈值 25%）——欠定时质量上限由信息量决定，与 λ 无关。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
