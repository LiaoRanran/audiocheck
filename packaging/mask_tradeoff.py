#!/usr/bin/env python3
"""mask_tradeoff：软掩码变体的**实测代价与收益**——用数据决定它们进不进内核。

    python packaging/mask_tradeoff.py

⚠ 这三个变体**只存在于本探针里**，不在 `core/edit/`。本仓的规矩是
"先量后改、不许凭空加机制"（`docs/editor_research.md §6` 表 2 把"留谱底 + 增益平滑"
列为待办），所以先把它们的**账**算出来：

  * **谱底**（`max(gain, −40 dB)`）：花掉多少**压制度**，买到什么？
  * **时间锥形**（选区两端各 `T` 秒内把增益从 1 平滑到 g）：花掉多少**时间精度**？
  * **写出的跨度不外扩** 时锥形能不能把边界台阶压下去？（我们已经外扩了 ⇒ 这可能是假需求）

素材 = 2 s 的 8 kHz 单频（与 `stft_bounds.py case_1` 同一份，数字可比），
四个测量列都是 `core/edit/metrics.py` 的同一把尺子。
"""
from __future__ import annotations

import os
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import stft  # noqa: E402
from audiocheck.core.edit.metrics import band_gain_db  # noqa: E402

FS = 48000
N = 4096
HOP = N // 4
K0, K1 = int(0.5 * FS), int(1.0 * FS)          # 时间选区 [0.5, 1.0) s
F0, F1 = 7000.0, 9000.0                        # 频率选区
G_DB = -60.0                                   # 设定压制度
TAPER = 0.05                                   # 时间锥形长度（秒）
FLOOR_DB = -40.0                               # 谱底


def variant(sig: np.ndarray, *, wrap: str, mode: str) -> np.ndarray:
    """把 `mode` 的掩码 + `wrap` 的写回方式作用到 `sig` 上（单块，素材 < chunk）。"""
    cs, ce = K0 - N, K1 + N
    buf = sig[cs - N:ce + N].copy()
    S, meta = stft.analyze(buf, n_fft=N, hop=HOP, pad=0)
    bins = stft.bin_freqs(FS, N)
    times = stft.frame_times(FS, N, HOP, S.shape[1], start_sample=cs - N)
    band = (bins >= F0) & (bins < F1)
    g = 10.0 ** (G_DB / 20.0)
    if mode.endswith("taper"):
        w = (np.clip((times - K0 / FS) / TAPER, 0, 1)
             * np.clip((K1 / FS - times) / TAPER, 0, 1))
        gain_f = 1.0 - w * (1.0 - g)                       # 逐帧增益（选区外 w=0 ⇒ 不动）
    else:
        gain_f = np.where((times >= K0 / FS) & (times < K1 / FS), g, 1.0)
    if mode.startswith("floor"):
        gain_f = np.maximum(gain_f, 10.0 ** (FLOOR_DB / 20.0))
    S[np.ix_(band, np.ones(S.shape[1], dtype=bool))] *= gain_f[None, :, None]
    span = stft.synthesize(S, meta, keep=slice(N, N + (ce - cs)))
    out = sig.copy()
    if wrap == "wide":                                     # 当前实现：整段跨度写回
        out[cs:ce] = span
    else:                                                  # 假想的"硬切"版本（会留台阶）
        out[K0:K1] = span[K0 - cs:K1 - cs]
    return out


def report(sig, label, foot: str):
    print("%-18s %-16s %10s %10s %10s %10s"
          % (label, "变体", "带内(核心)", "边界+5ms", "带外3-5k", "包络跳变"))
    for wrap in ("wide", "cut"):
        for mode in ("hard", "hard+taper", "floor", "floor+taper"):
            out = variant(sig, wrap=wrap, mode=mode)
            core = band_gain_db(sig, out, FS, F0, F1, K0 + 2 * N, K1 - 2 * N)
            edge = band_gain_db(sig, out, FS, F0, F1, K0 + int(0.005 * FS), K0 + int(0.025 * FS))
            outb = band_gain_db(sig, out, FS, 3000.0, 5000.0, K0 + 2 * N, K1 - 2 * N)
            env = np.abs(signal.hilbert(out[:, 0]))
            step = float(np.abs(np.diff(env)).max())
            print("%-18s %-16s %9.2f %10.2f %10s %10.1e"
                  % (label, "%s/%s" % (wrap, mode),
                     -99.0 if core is None else core,
                     -99.0 if edge is None else edge,
                     "—" if outb is None else "%.2f" % outb, step))
    print("   ↑ %s" % foot)
    print()


def main() -> None:
    t = np.arange(2 * FS) / FS
    tone = (0.2 * np.sin(2 * np.pi * 8000 * t))[:, None]
    two = (0.2 * np.sin(2 * np.pi * 8000 * t) + 0.1 * np.sin(2 * np.pi * 4000 * t))[:, None]
    print("设定 %.0f dB ｜ 选区 [%.1f, %.1f) s × [%.0f, %.0f) Hz ｜ 锥形 %.0f ms ｜ 谱底 %.0f dB\n"
          % (G_DB, K0 / FS, K1 / FS, F0, F1, TAPER * 1000, FLOOR_DB))
    print("四列都是 `metrics.band_gain_db` 实测 dB（带外那列）+ Hilbert 包络逐采样最大跳变。\n")
    report(tone, "单频 8 kHz",
           "材料只有一条 8 kHz 正弦 ⇒ **包络跳变**列才有效；「带外」列量到的是这条正弦自己的泄漏，不算数")
    report(two, "8 kHz + 4 kHz",
           "材料在 3–5 kHz 有**真实内容** ⇒ **带外**列才有效（应当≈0，证明掩码没碰别的频段）")

    print("结论（据此决定，不照文献搬）：")
    print("1. **谱底 −40 dB**：把核心压制度 −60 砍到 −40（花掉 20 dB），包络跳变一模一样"
          "（1.4e-04），边界 +5ms 处只是从 −42.3 变到 −36.4 ⇒ **买不到「更干净」的证据** ⇒ 不进内核。")
    print("2. **时间锥形 50 ms**：核心压制度不变，但**边界 +5ms 处从 −42.3 dB 掉到 −3.1 dB**"
          "（选区内头 50 ms 几乎没被压）—— 用 50 ms 的选区精度换包络跳变 1.4e-04→8.7e-05"
          "（**只差 1.6 倍，且本就在噪声级**）⇒ 不进内核。")
    print("3. **「不外扩跨度、靠锥形压台阶」是假需求**：实测 `cut/hard+taper` 把台阶从 7.6e-02 压到"
          " 1.0e-02 —— 仍比我们现在（`wide/hard` = 1.4e-04）**差 70 倍**，还要付 50 ms 精度代价"
          " ⇒ 已有的组合（外扩跨度 + 硬掩码）**已在最优格**。")


if __name__ == "__main__":
    main()

