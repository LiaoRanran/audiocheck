#!/usr/bin/env python3
"""hum_detect_probe：去嗡的 **f0 自动检测**先量口径（P3 挂账项，判据先行）。

## 为什么难

"50/100/150 Hz 上有三条线"**不足以**说明是嗡——一把 55 Hz 的贝斯也有 110/165。
真正区分嗡的不是"谐波族"，而是**稳态**：嗡在整段里一直在（连"安静"的帧里也在），
频率与幅度几乎不随时间变；乐音有包络、起音、揉弦。

## 候选口径（都在**谱图**上量，不在平均谱上量——平均会把时间信息抹掉）

对候选基频 `f0` 与每个谐波 `k`（1..3）：

* `excess[k]` = 该线在帧间的**中位数** − 同帧 ±10 Hz 邻域中位数（dB）；
* `stab[k]`  = 该线帧间水平的 **p95 − p5**（dB，越小越稳态）；
* `floor_share` = "该线在**最安静的 20% 帧**里仍高出邻域 ≥ 若干 dB"的比例。

判定：三条谐波都满足 `excess ≥ E` 且 `stab ≤ S` ⇒ 接受该 `f0`；否则**拒绝自动检测**
（CLI 仍要求用户显式给 `--hum-f0`）。

本探针就是来**量 E 与 S 该取多少**：把合成嗡（不同电平/基频/素材）与
**乐音对照**（55 / 41.2 / 82.4 Hz 的衰减贝斯音，含 6 次谐波）放一起比。

    python packaging/hum_detect_probe.py
"""
from __future__ import annotations

import sys

import numpy as np
from scipy import signal

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus  # noqa: E402

FS = 48000
SECONDS = 6.0
NPERSEG = 16384
HOP = 4096
K = 3


def hum(clean: np.ndarray, f0: float, rel_db: float, fs: int = FS, seed: int = 7) -> np.ndarray:
    n = clean.shape[0]
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    h = np.zeros(n)
    for k in (1, 2, 3):
        h += np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 2 * np.pi)) / k
    h *= (10 ** (rel_db / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    return clean + h[:, None]


def bass_note(f0: float, fs: int = FS, seconds: float = SECONDS,
              decay_s: float = 0.35, amp: float = 0.5, seed: int = 3) -> np.ndarray:
    """衰减贝斯音：6 次谐波 + 指数包络 + 轻微揉弦（±0.3%，5.5 Hz）。"""
    n = int(seconds * fs)
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    vib = 1.0 + 0.003 * np.sin(2 * np.pi * 5.5 * t)
    phase = 2 * np.pi * np.cumsum(f0 * vib) / fs
    x = np.zeros(n)
    for k in range(1, 7):
        x += (1.0 / k) * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    env = np.exp(-t / decay_s)
    x = x / float(np.abs(x * env).max()) * amp * env
    # 每 1.5 s 重新起音（"有音符在弹"而不是一直响）
    gate = (np.floor(t / 1.5) % 2 == 0).astype(float)
    gate = np.convolve(gate, np.hanning(256) / np.hanning(256).sum(), mode="same")
    return (x * gate)[:, None]


def lines(x: np.ndarray):
    """**与实现同一口径**：帧间中位数谱找线（抛物线插值）+ 三条线的三项证据。"""
    from audiocheck.core.analyzers.hum import _lines, _peak_hz
    f, _t, Z = signal.stft(x[:, 0], fs=FS, nperseg=NPERSEG, noverlap=NPERSEG - HOP,
                           window="hann", boundary=None, padded=False)
    mag = 20.0 * np.log10(np.abs(Z) + 1e-30)
    M = np.median(mag, axis=1)
    return float(_peak_hz(f, M)), _lines(mag, f, float(_peak_hz(f, M)))


def best(rows):
    """按"三线 excess 的最小值"排序，取最好的一组（口径本身也要被量）。"""
    scored = sorted(rows, key=lambda r: min(r[1]), reverse=True)
    return scored[0]


MATERIALS = {}
clean_noise = corpus.band_limited_noise(SECONDS, FS, 18000.0, amp=0.5)
clean_tone = corpus.harmonic_tone(SECONDS, FS, 440.0, 40, amp=0.5)
MATERIALS["白噪（无嗡）"] = clean_noise
MATERIALS["白噪 + 50Hz 嗡 −20dB"] = hum(clean_noise, 50.0, -20.0)
MATERIALS["白噪 + 60Hz 嗡 −30dB"] = hum(clean_noise, 60.0, -30.0)
MATERIALS["谐波音 + 50Hz 嗡 −30dB"] = hum(clean_tone, 50.0, -30.0)
MATERIALS["贝斯 55Hz（乐音对照）"] = bass_note(55.0)
MATERIALS["贝斯 41.2Hz（乐音对照）"] = bass_note(41.2)
MATERIALS["贝斯 82.4Hz（乐音对照）"] = bass_note(82.4)
MATERIALS["贝斯 55Hz + 50Hz 嗡 −30dB"] = hum(bass_note(55.0), 50.0, -30.0)

print(" %-30s %6s %8s %-22s %-22s %s"
      % ("素材", "检出f0", "min excess", "excess(1/2/3)", "stab(1/2/3)", "安静帧 excess(1/2/3)"))
for name, x in MATERIALS.items():
    f0, ev = lines(x)
    print(" %-30s %6.2f %8.1f %-22s %-22s %s"
          % (name, f0, min(e for e, _s, _q in ev),
             "/".join("%.1f" % e for e, _s, _q in ev),
             "/".join("%.1f" % s for _e, s, _q in ev),
             "/".join("%.1f" % q for _e, _s, q in ev)))
