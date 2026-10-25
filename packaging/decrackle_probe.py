#!/usr/bin/env python3
"""decrackle_probe：**密集小脉冲**（破音/噼啪）能不能用现有去咔哒算子修？（先量，再决定）

## 问题

`docs/editor_roadmap.md §4` 的 P3 还列着 `decrackle`。但"噼啪"与"咔哒"在采样域里
是同一类东西（孤立/密集的脉冲），差别是**密度**。先问：现有 `DeclickOp`
（两点预测残差 + 全段 MAD + 宽度守卫 4）在密集脉冲上到底表现如何？

## 量什么

`corpus.crackle_train`：2 s 谐波音 + `rate_per_s` 个位置/幅度已知的小脉冲
（幅度 0.02–0.2 对数均匀、极性随机）。三个密度：10 / 100 / 500 个每秒。

* **ΔSDR** vs 干净真值（主判据）；
* **TPR**：真值位置里被检出的比例；**precision**：检出样本里真是脉冲的比例；
* **改动面** / **被宽度守卫挡下的段数**；
* 原始检出段的**宽度分布**（宽度 >4 的占比）——解释守卫为什么会挡。

候选参数（不新增算法，先看参数够不够）：
`threshold` 5/3/8 × `max_run` 4/None。

    python packaging/decrackle_probe.py
"""
from __future__ import annotations

import sys

import numpy as np
from scipy.linalg import solve_toeplitz
from scipy.signal import lfilter

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus, declick  # noqa: E402
from audiocheck.core.edit.declick import _runs  # noqa: E402
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
T = 2.0
RATES = (10.0, 100.0, 500.0)
AMP_RANGES = ((0.02, 0.2), (0.05, 0.5))
CANDIDATES = ((5.0, 4), (5.0, None), (3.0, None), (8.0, None), (8.0, 4))
LPC_ORDER = 64


def build(rate: float, amp_min: float, amp_max: float, seed: int = 5):
    clean = corpus.harmonic_tone(T, FS, amp=0.5, seed=3)
    return corpus.crackle_train(clean, rate, FS, amp_min=amp_min, amp_max=amp_max,
                               seed=seed)


def _raw_runs(y):
    raw, _r, _s = declick._detect_raw(y[:, 0], 5.0)
    return [e - s for s, e in _runs(raw)]


def measure(pack, threshold, max_run):
    clean, y = pack["clean"], pack["crackled"]
    out, st = declick.declick(y, threshold, max_run)
    pos = np.array(pack["positions_samples"])
    detected = out[:, 0] != y[:, 0]
    tpr = float(detected[pos].mean()) if pos.size else 0.0
    precision = float(detected[pos].sum() / max(1, detected.sum()))
    return {
        "dsdr": sdr_db(out, clean) - sdr_db(y, clean),
        "er": energy_ratio_db(out, clean),
        "tpr": tpr,
        "precision": precision,
        "frac": st["fraction"],
        "rejected": st["rejected_runs"],
    }


def _lpc_residual(x, order=LPC_ORDER):
    """LPC 残差（全段自相关 + Toeplitz 求解；FFT 算自相关，避免 O(n²)）。"""
    a = x - x.mean()
    n = len(a)
    X = np.fft.rfft(a, 2 * n)
    ac = np.fft.irfft(np.abs(X) ** 2)[:order + 1]
    coef = solve_toeplitz(ac[:order], ac[1:order + 1])
    return lfilter(np.r_[1.0, -coef], [1.0], a)


def measure_lpc(pack, threshold=5.0):
    """第二个候选：**LPC(64) 残差**检测（音乐可预测、脉冲不可预测）→ 同一套插值修复。"""
    clean, y = pack["clean"], pack["crackled"]
    r = _lpc_residual(y[:, 0])
    s = float(np.median(np.abs(r)) / 0.6745)
    mask = np.abs(r) > threshold * s if s > 0 else np.zeros(len(r), dtype=bool)
    out = declick._repair_runs(y[:, 0], mask)[:, None]
    pos = np.array(pack["positions_samples"])
    detected = mask
    tpr = float(detected[pos].mean()) if pos.size else 0.0
    precision = float(detected[pos].sum() / max(1, detected.sum()))
    return {
        "dsdr": sdr_db(out, clean) - sdr_db(y, clean),
        "er": energy_ratio_db(out, clean),
        "tpr": tpr,
        "precision": precision,
        "frac": float(detected.mean()),
        "rejected": 0,
        "ratio": float(np.abs(r).max() / s) if s > 0 else 0.0,
    }


def main() -> int:
    print("破音（密集小脉冲）：现有去咔哒 / LPC 残差的表现（2 s 谐波音，位置与幅度已知）\n")
    print("%-8s %-9s %-9s %-10s %8s %8s %7s %9s %8s %9s" %
          ("幅度", "密度/s", "检出", "max_run", "ΔSDR", "能量比", "TPR",
           "precision", "改动面%", "挡下段"))
    print("-" * 100)
    for amp_min, amp_max in AMP_RANGES:
        for rate in RATES:
            pack = build(rate, amp_min, amp_max)
            widths = _raw_runs(pack["crackled"])
            wide = sum(1 for w in widths if w > 4)
            print("%-8s %-9g 原始段 %d（中位 %d / 最大 %d / >4 的 %d）"
                  % ("%.2f–%.2f" % (amp_min, amp_max), rate, len(widths),
                     int(np.median(widths)) if widths else 0,
                     max(widths) if widths else 0, wide))
            for th, mr in CANDIDATES:
                m = measure(pack, th, mr)
                print("%-8s %-9g %-9s %-10s %+8.2f %+8.2f %7.3f %9.3f %8.3f %9d"
                      % ("", rate, "2点%.0f" % th, "关" if mr is None else mr,
                         m["dsdr"], m["er"], m["tpr"], m["precision"],
                         100.0 * m["frac"], m["rejected"]))
            lm = measure_lpc(pack)
            print("%-8s %-9g %-9s %-10s %+8.2f %+8.2f %7.3f %9.3f %8.3f %9s"
                  % ("", rate, "LPC64", "关", lm["dsdr"], lm["er"], lm["tpr"],
                     lm["precision"], 100.0 * lm["frac"],
                     "max/σ=%.2f" % lm["ratio"]))
            print("-" * 100)
    return 0


if __name__ == "__main__":
    sys.exit(main())
