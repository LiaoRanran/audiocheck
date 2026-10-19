#!/usr/bin/env python3
"""sparse_declip_probe：**密集削波要的那条先验（时频稀疏）到底能不能打**——先量再改。

    python packaging/sparse_declip_probe.py

背景（本轮的**实测**，见 `docs/editor_research.md §10`）：`declip.py` 现用的三相交替投影
在 **severe**（饱和 ≈23%）上**加到 8000 次迭代也只到 13.7 dB**（基线 10.1），过松弛也没用；
把**非凸**的第三相交（`|x| ≥ c`，球的补集）去掉、只留两个**凸**集，同样卡在 11.1 dB。
⇒ 这说明卡点是**可行集的歧义（欠定）**，不是收敛速度 ⇒ 必须有**额外的先验**。
文献给的先验是**时频稀疏**（Adler 2012 的 analysis/synthesis 稀疏；Záviška 的 SPADE 谱系）。

本探针**只做决策实验**（候选求解器只活在这里）：用**同一把尺子**（`sdr_db` + `energy_ratio_db`）
量两族：
* **POCS**（现有，`core/edit/declip.py` 的算法）——凸两相交 + 不等式；
* **ADMM/analysis-ℓ1**：`minimize ‖A x‖₁ s.t. x ∈ C`，`A` = 本项目自己的 WOLA STFT，
  解用 ADMM（z-update = 复系数**软阈值**，x-update = 投影回 `C`）。
  ⚠ 关键是 `S A = I` 且 `A*A = diag(窗平方和)`（**逐样本**），所以 x-update 有**闭式**：
  `x ← P_C(S(z − u))`——不是近似，不是调参。
* 另量：**幅度缩水**（PHAIN 的警告：ℓ1 会把幅度压小）、**不等式违反数**、耗时。

⚠ 阈值 `α`（软阈值水平 = `α · max|A y|`）**不做玄学默认值**：扫一遍并报出敏感性。
"""
from __future__ import annotations

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import corpus, declip, stft  # noqa: E402
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
B = 0.9 * FS / 2.0
N_FFT = 4096
HOP = N_FFT // 4
SECONDS = 1.0
ALPHAS = (0.003, 0.01, 0.03, 0.1, 0.3)      # 软阈值水平 α（相对 `max|A y|`）
ITERS = (16, 64, 256)


def _project(x, y, ceiling, mask_known, mask_sat, sign, fs, bandwidth):
    """`P_C`：带限（整段 FFT 砍 `> B` 的 bin）→ 已知样本写回 → 不等式钳位（同号）。"""
    X = np.fft.rfft(x, axis=0)
    X[np.fft.rfftfreq(x.shape[0], 1.0 / fs) > bandwidth] = 0.0
    out = np.fft.irfft(X, n=x.shape[0], axis=0)
    out[mask_known] = y[mask_known]
    out[mask_sat] = sign[mask_sat] * np.maximum(np.abs(out[mask_sat]), ceiling)
    return out


def recon_admm(y, ceiling, fs, bandwidth, alpha=0.03, iters=64,
               n_fft=N_FFT, hop=HOP, pad=None):
    """**analysis-ℓ1 的 ADMM**（时频稀疏先验）：`minimize ‖A x‖₁ s.t. x ∈ C`。

    `A` = 本项目的 WOLA STFT（`stft.analyze`，`pad=n_fft` 自带上下文 ⇒ `Σ w² > 0` 处处成立），
    `S` = `stft.synthesize`（**逐样本**归一化 ⇒ `S = A*/diag(Σw²)` 且 `S A = I`）。
    x-update：`x ← P_C(S(z − u))`（闭式，见模块 docstring）；z-update：复系数软阈值；
    u-update：对偶上升。返回 `(out, stats)`。
    """
    a = np.asarray(y, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    pad = n_fft if pad is None else pad
    sat = np.abs(a) >= ceiling
    known = ~sat
    sign = np.where(a >= 0.0, 1.0, -1.0)

    S, meta = stft.analyze(a, n_fft=n_fft, hop=hop, pad=pad)
    thresh = float(alpha) * float(np.abs(S).max())
    z = S.copy()
    u = np.zeros_like(S)
    x = a.copy()
    for _ in range(int(iters)):
        v = z - u
        x = _project(stft.synthesize(v, meta), a, ceiling, known, sat, sign, fs, bandwidth)
        Ax = stft.analyze(x, n_fft=n_fft, hop=hop, pad=pad)[0]
        t = np.abs(Ax + u)
        z = (Ax + u) * (np.maximum(t - thresh, 0.0) / np.maximum(t, 1e-300))   # 复软阈值
        u = u + Ax - z
    return x, {"thresh": thresh, "violations": int((np.abs(x[sat]) < ceiling).sum())}


def _report(name, est, pack, extra=""):
    s = sdr_db(est, pack["clean"])
    e = energy_ratio_db(est, pack["clean"])
    print("  %-22s SDR %7.2f dB ｜ 能量 %+7.2f dB %s"
          % (name, -999.0 if s is None else s, 0.0 if e is None else e, extra))


def materials():
    """**两种稀疏性相反**的素材——先验的成不成立**取决于素材**，只量一种等于没量（本仓教训）。

    * `band_limited_noise`：严格带限的白噪 ⇒ 在**任何**时频字典里都**最不稀疏**；
    * `harmonic_tone`：几十条谐波线 ⇒ 在时频字典里**高度稀疏**（这才是文献那套先验的适用域）。
    """
    yield "带限白噪（不稀疏）", lambda: corpus.band_limited_noise(SECONDS, fs=FS,
                                                              bandwidth=18000.0, seed=1)
    yield "谐波音（稀疏）", lambda: corpus.harmonic_tone(SECONDS, fs=FS, f0=440.0,
                                                     n_partials=40, seed=2)


def underdetermined_table() -> None:
    """**欠定判据的验证**（`declip.py` 的 `ambiguity_margin`）：同一份 severe 素材，`B` 从小扫到
    大、**跨越门槛 `1 − B/(fs/2)`** ⇒ 看 POCS 是不是从"随迭代变好"变成"全程钉住"。

    这一节是"可辨识性预判"这条**产品行为**的证据：门槛两侧的行为**是可预测的**。
    """
    p = corpus.clipped(corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1),
                       "severe")
    print("=" * 100)
    print("欠定判据验证（severe，f=%.1f%%，语料**真值** B=18 kHz；解集维数/N ≈ B/(fs/2)−(1−f)）"
          % (100 * p["stats"]["fraction"]))
    print("%9s %10s %10s ｜ %s" % ("给的 B", "门槛 f>", "余量", "POCS SDR（400 / 2000 / 8000 次）"))
    for b in (18000.0, 19000.0, 20000.0, 21000.0, 21600.0):
        margin = (1 - p["stats"]["fraction"]) - b / (FS / 2)
        row = "%9.0f %9.1f%% %+10.3f ｜" % (b, 100 * (1 - b / (FS / 2)), margin)
        for it in (400, 2000, 8000):
            out, _ = declip.reconstruct(p["clipped"], p["ceiling"], FS, b, iterations=it)
            row += " %8.2f" % sdr_db(out, p["clean"])
        print(row)
    print("  读法：余量**为正（定解）**时 SDR 随迭代**一直在涨**；余量**≤0（欠定）**时全程钉住"
          "（400→8000 次几乎不动）⇒ `DeclipOp` 的 `ambiguous` 标志据此给。")


def main() -> None:
    print("稀疏去削波探针 ｜ fs=%d ｜ 素材 %.0f s ｜ n_fft=%d hop=%d（与本项目编辑内核同参数）"
          % (FS, SECONDS, N_FFT, HOP))
    print("尺子：`metrics.sdr_db` + `metrics.energy_ratio_db`（同一把）；另报不等式违反数与耗时\n")
    for mname, make in materials():
        print("=" * 100)
        print("素材：%s" % mname)
        for tier in ("light", "moderate", "severe"):
            p = corpus.clipped(make(), tier)
            y, c = p["clipped"], p["ceiling"]
            sat = int((np.abs(y) >= c).sum())
            print("  %s（饱和 %d / %d = %.1f%%）" % (tier, sat, y.shape[0],
                                                     100 * sat / y.shape[0]))
            _report("基线（削波信号本身）", y, p)
            for it in (128, 400):
                _report("POCS %d 次" % it, declip.reconstruct(y, c, FS, B, iterations=it)[0], p)
            for al in (0.003, 0.01, 0.03):
                for it in (64, 256):
                    t0 = time.time()
                    out, st = recon_admm(y, c, FS, B, alpha=al, iters=it)
                    _report("ADMM α=%-5g %d 次" % (al, it), out, p,
                            "｜ 违反不等式 %d ｜ %.2f s" % (st["violations"], time.time() - t0))
        print()
    underdetermined_table()


if __name__ == "__main__":
    main()
