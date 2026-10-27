#!/usr/bin/env python3
"""denoise_noisecheck_probe：**"噪声段混入内容"能被自动查出来吗？**（先量，再定阈值）

## 问题（P3 去噪挂账的最后一项）

`DenoiseOp` 的契约是"噪声段必须只有噪声"——但用户经常拿错（拿了一段带音乐声的"安静处"）。
契约被违反的代价已实测（`denoise_probe.py`：把内容当噪声减掉，ΔSDR −8.7 dB 量级）。
本探针找一个**确定性、可复算**的统计量把"纯噪声段"和"混入内容的段"分开：

* **局部谱突出度**：平均谱（dB）里每个 bin 相对**±k bin 局部中值**的超出量（dB）取最大。
  纯噪声的谱起伏是平滑的（±几 dB）；内容（音乐泛音列/纯音）会有一根**孤峰**高出局部
  底噪十几 dB ⇒ 该统计量对"混入内容"敏感、对"噪声种类"不敏感。

量三种纯噪声（白/粉/带限）× 三档 SNR 的混入内容（谐波音/纯音），画出两组分布，
选一个**纯噪声全过、混入全报**的阈值。

    python packaging/denoise_noisecheck_probe.py
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, "src")
from audiocheck.core.edit import (
    corpus,  # noqa: E402
    stft,  # noqa: E402
)

FS = 48000
SEC = 2.0
N_FFT = 4096
HALF_BINS = 16            # 局部中值的半窗（bin）——4096-FFT @48k ⇒ 1 bin ≈ 11.7 Hz


def prominence_db(seg: np.ndarray, half: int = HALF_BINS) -> float:
    """平均谱的**局部突出度**（dB）：max(每 bin 谱dB − ±half bin 的局部中值谱dB)。"""
    S, _meta = stft.analyze(seg, n_fft=N_FFT, hop=N_FFT // 4, pad=0)
    avg = np.mean(np.abs(S) ** 2, axis=1)                     # 平均功率谱
    db = 10.0 * np.log10(np.maximum(avg, 1e-30))
    med = np.empty_like(db)
    for i in range(db.size):                                  # 截断窗（与 mirror 同义）
        lo, hi = max(0, i - half), min(db.size, i + half + 1)
        med[i] = np.median(db[lo:hi])
    return float(np.max(db - med))


def main() -> int:
    print("局部谱突出度（dB，越大越像混入了内容；N_FFT=%d，局部中值 ±%d bin）\n"
          % (N_FFT, HALF_BINS))
    print("== 纯噪声段（应当**低**）==")
    pure = []
    for label, mk in (
        ("白噪 +10dB", lambda: corpus.add_noise(corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3),
                                                10.0, "white", seed=21)["noise"]),
        ("白噪 0dB", lambda: corpus.add_noise(corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3),
                                              0.0, "white", seed=22)["noise"]),
        ("粉噪 0dB", lambda: corpus.add_noise(corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3),
                                              0.0, "pink", seed=23)["noise"]),
        ("带限白噪", lambda: corpus.band_limited_noise(SEC, fs=FS, bandwidth=18000.0, seed=24)),
    ):
        v = prominence_db(mk())
        pure.append(v)
        print("  %-12s %.2f dB" % (label, v))
    print("== 混入内容的段（应当**高**）==")
    mixed = []
    for label, mk in (
        ("谐波音混白噪 +6dB", lambda: corpus.add_noise(
            corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3), 6.0, "white", seed=31)["noisy"]),
        ("谐波音混白噪 +20dB", lambda: corpus.add_noise(
            corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3), 20.0, "white", seed=32)["noisy"]),
        ("纯音 1kHz 混白噪 +20dB", None),
        ("滑音混白噪 +20dB", None),
    ):
        if mk is None:
            t = np.arange(int(SEC * FS)) / FS
            nz = corpus.white_noise(SEC, FS, rms=0.01, seed=33)
            if label.startswith("纯音"):
                seg = (0.3 * np.sin(2 * np.pi * 1000.0 * t))[:, None] + nz
            else:
                f = 300.0 + 400.0 * t / SEC
                ph = 2 * np.pi * np.cumsum(f) / FS
                seg = (0.3 * np.sin(ph))[:, None] + nz
        else:
            seg = mk()
        v = prominence_db(seg)
        mixed.append(v)
        print("  %-24s %.2f dB" % (label, v))
    print("\n分布：纯噪声 [%.2f, %.2f] ｜ 混入内容 [%.2f, %.2f]"
          % (min(pure), max(pure), min(mixed), max(mixed)))
    gap_lo, gap_hi = max(pure), min(mixed)
    if gap_hi > gap_lo:
        thr = round((gap_lo + gap_hi) / 2.0, 1)
        print("⇒ **可分**：建议阈值 %.1f dB（纯噪声全过、混入全报）" % thr)
    else:
        print("⇒ **不可分**：两组分布重叠，此统计量不立判据")
    return 0


if __name__ == "__main__":
    sys.exit(main())
