#!/usr/bin/env python3
"""stft_bounds：时频编辑三条**效力边界**的可复算探针（对应 `docs/theory.md §12.7`）。

    python packaging/stft_bounds.py

输出三组数，每组都对应文档里的一句结论——**文档里的数字必须是这里跑出来的**，
不许手算当实测（本仓铁律："能算的绝不猜"，以及"每个数字标出处"）：

  ① **边界台阶**：写出的跨度**不外扩** vs **外扩一个窗宽**——8 kHz 成分在选区边界上的
     包络跳变。结论：向外扩 `n_fft` 不是保险，是时变滤波器的定义（Portnoff 1980）。
  ② **float32 量化底**：素材全频段的底噪水平（相对谱峰多少 dB）+ 一个"带外掩码"造成的
     波形改动，与**峰值量级的 1 个 float32 ULP** 相比。结论：float32 素材只能承诺
     "≤ 1 个峰值 ULP"，"逐位不变"只对 float64 成立。
  ③ **覆盖放大**：`pad=0` 且**不自带上下文**时，未覆盖区被 `1/Σw²` 放大的倍数。
     结论：可重建区间必须过覆盖守卫（`synthesize` 抛错），不许静默置 0 或产出放大噪声。
"""
from __future__ import annotations

import math
import os
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import (
    render_band,  # noqa: E402
    stft,  # noqa: E402
)

FS = 48000
N = 4096
HOP = N // 4


def _tmp(name):
    return os.path.join(tempfile.gettempdir(), "ac_stft_bounds_" + name)


def case_1_boundary_step():
    """① 不外扩 vs 外扩：选区边界上的包络跳变。

    两条路径共用**同一次**掩码合成（`out_span` = 跨度 `[k0−N, k1+N)` 上的重建结果），
    只有"写回哪里"不同：
      * **外扩**（当前实现）：把跨度整段写回；
      * **硬切**（不外扩）：只把选区分段 `[k0, k1)` 写回，两侧留原信号。
    ⚠ 取样必须只取**中间段**（`keep` 从 `N` 起）：两侧 `N` 是上下文，覆盖不足（见守卫）。
    """
    t = np.arange(2 * FS) / FS
    x = 0.2 * np.sin(2 * np.pi * 8000 * t)
    sig = np.column_stack([x, x])
    k0, k1 = int(0.5 * FS), int(1.0 * FS)
    cs, ce = k0 - N, k1 + N                       # 写出的跨度 = 选区 ± 一个窗宽
    buf = sig[cs - N:ce + N].copy()               # 自带上下文（各一个窗宽）
    S, meta = stft.analyze(buf, n_fft=N, hop=HOP, pad=0)
    bins = stft.bin_freqs(FS, N)
    times = stft.frame_times(FS, N, HOP, S.shape[1], start_sample=cs - N)
    band = (bins >= 7000.0) & (bins < 9000.0)
    tm = (times >= 0.5) & (times < 1.0)
    S[np.ix_(band, tm)] *= 10.0 ** (-60.0 / 20.0)
    span = stft.synthesize(S, meta, keep=slice(N, N + (ce - cs)))     # 跨度上的重建

    widened = sig.copy()
    widened[cs:ce] = span                                             # 当前实现（外扩）
    hard = sig.copy()
    hard[k0:k1] = span[k0 - cs:k1 - cs]                               # 不外扩（硬切）

    def env_stats(sigx):
        e = np.abs(signal.hilbert(sigx[:, 0]))
        return e, float(np.abs(np.diff(e)).max())

    ew, step_w = env_stats(widened)
    eh, step_h = env_stats(hard)
    print("① 边界台阶（素材 8 kHz / 0.2，掩码 −60 dB，选区 [0.5, 1.0) s）")
    print("   外扩：边界前 %.4f → 边界处 %.4f ｜ 逐采样最大跳变 %.2e"
          % (ew[k0 - 1], ew[k0], step_w))
    print("   硬切：边界前 %.4f → 边界处 %.4f ｜ 逐采样最大跳变 %.2e"
          % (eh[k0 - 1], eh[k0], step_h))
    print("   ⇒ 硬切/外扩 = %.0f 倍；判据阈值 0.01（`test_band_boundary_has_no_step`）\n"
          % (step_h / max(step_w, 1e-300)))


def case_2_float32_floor():
    """② float32 量化底：底噪水平 + 带外掩码造成的波形改动 vs 峰值 ULP。"""
    t = np.arange(2 * FS) / FS
    x = 0.3 * np.sin(2 * np.pi * 440 * t)
    a = np.column_stack([x, x]).astype(np.float32)
    src, dst = _tmp("q.wav"), _tmp("qd.wav")
    sf.write(src, a, FS, subtype="FLOAT")
    render_band(src, dst, 0.5, 1.0, 3000.0, 9000.0, -40.0, overwrite=True)  # 440 在带外
    s = sf.read(src, dtype="float64", always_2d=True)[0]
    d = sf.read(dst, dtype="float64", always_2d=True)[0]
    k0, k1 = int(0.5 * FS), int(1.0 * FS)
    buf = s[k0 - N:k1 + N].copy()
    S, meta = stft.analyze(buf, n_fft=N, hop=HOP, pad=0)
    bins = stft.bin_freqs(FS, N)
    b = (bins >= 3000.0) & (bins < 9000.0)
    floor_db = 20 * math.log10(np.abs(S[b]).max() / np.abs(S).max())
    diff = np.abs(d[k0 + N:k1 - N] - s[k0 + N:k1 - N]).max()
    peak_ulp = float(np.spacing(np.float32(np.abs(a).max())))
    print("② float32 量化底（素材 0.3 的 440，带外掩码 [3000,9000) × −40 dB）")
    print("   3–9 kHz 谱相对谱峰：%.1f dB（24 位 float32 的底）" % floor_db)
    print("   带外波形改动 max|Δ| = %.3e（= 2^%.1f）｜ 峰值 ULP = %.3e ⇒ 比值 %.2f"
          % (diff, math.log2(diff), peak_ulp, diff / peak_ulp))
    print("   ⇒ 判据：float32 源 ≤ 1 个峰值 ULP；逐位不变只对 float64 源承诺\n")


def case_3_coverage_amplification():
    """③ 覆盖放大：pad=0 不自带上下文时，未覆盖区被放大多少。"""
    rng = np.random.default_rng(5)
    x = (rng.standard_normal((FS, 1)) * 0.2)
    print("③ 覆盖放大（白噪 0.2，pad=0 直接喂整段 1 s）")
    S, meta = stft.analyze(x, n_fft=N, hop=HOP, pad=0)
    try:
        stft.synthesize(S, meta)
    except ValueError as e:
        print("   覆盖守卫已拦下：%s" % str(e)[:60] + "…")
    # 绕过守卫量一下"如果静默除下去会怎样"（探针允许用内部函数）
    blk = np.fft.irfft(np.transpose(S, (1, 2, 0)), n=N, axis=-1) * meta["win"]
    ola, wss = stft._overlap_add(blk, meta["win"], HOP)
    tiny = wss > 1e-12
    bad = np.zeros_like(ola)
    bad[tiny] = ola[tiny] / wss[tiny, None]
    lp = bad.shape[0]
    # ⚠ 帧网被截断 ⇒ 可重建长度 `lp` **短于**输入长度，这里只比两者都有定义的那一段
    tail = bad[lp - N:lp, 0] - x[lp - N:lp, 0]
    print("   未覆盖区（末一个窗宽）的 |重建 − 源| 最大 %.3e，源本身 |x| 最大 %.3e"
          % (np.abs(tail).max(), np.abs(x).max()))
    print("   ⇒ 放大倍数 %.3g ｜ 结论：这种区间必须抛错，不许静默置 0\n"
          % (np.abs(tail).max() / np.abs(x).max()))


if __name__ == "__main__":
    case_1_boundary_step()
    case_2_float32_floor()
    case_3_coverage_amplification()
