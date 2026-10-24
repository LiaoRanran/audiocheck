#!/usr/bin/env python3
"""dehum_probe：去嗡**先量候选**（P3 判据先行）。

## 为什么先量

`docs/editor_roadmap.md §5` 规定每个修复算子落地前必须先在**有真值**的合成语料上量
"提升了多少、代价是什么"。去嗡的真值极好造：`y = 干净 + 50/60 Hz 及其谐波`。

## 候选

A. **因果 IIR 陷波**（`iirnotch` 级联，逐谐波一个）——可流式、可分块；
B. **零相位陷波**（同一组陷波做 `filtfilt`）——离线、整段，理论上不引入相位失真；
C. **STFT 置零**（复用 `core/edit/stft` 的 WOLA）——只清掉谐波附近 ±若干 bin。

## 尺子（`core/edit/metrics.py`，同一把）

* `sdr_db(out, clean)` —— 与真值的距离（越高越好）；
* 每个谐波处的**带内增益** `band_gain_db(noisy, out, fs, kf0±5)` —— 压制了多少 dB；
* `energy_ratio_db(out, noisy)` —— 幅度有没有被顺手压小。

    python packaging/dehum_probe.py
"""
from __future__ import annotations

import sys
import time

import numpy as np
from scipy import signal

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus, stft  # noqa: E402
from audiocheck.core.edit.metrics import (  # noqa: E402
    band_gain_db,
    energy_ratio_db,
    sdr_db,
)

FS = 48000
SECONDS = 3.0
HARMONICS = (1, 2, 3)


def hum(clean: np.ndarray, f0: float, rel_db: float, harmonics=HARMONICS, fs: int = FS):
    """把嗡叠到干净信号上：幅度相对**干净信号峰值**（`rel_db`），相位用固定 seed。"""
    n = clean.shape[0]
    t = np.arange(n) / fs
    rng = np.random.default_rng(20261022)
    h = np.zeros(n)
    for k in harmonics:
        h += np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 2 * np.pi)) / k
    h *= (10 ** (rel_db / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    return clean + h[:, None]


def notches(f0, harmonics=HARMONICS, q=20.0):
    return [signal.iirnotch(f0 * k, q, FS) for k in harmonics]


def causal(x, f0, q):
    y = x
    for b, a in notches(f0, q=q):
        y = signal.lfilter(b, a, y, axis=0)
    return y


def zero_phase(x, f0, q):
    y = x
    for b, a in notches(f0, q=q):
        y = signal.filtfilt(b, a, y, axis=0)
    return y


def stft_zero(x, f0, width_hz=12.0, n_fft=4096, hop=1024):
    win = stft.default_window(n_fft)
    S, meta = stft.analyze(x, n_fft=n_fft, hop=hop, win=win, pad=n_fft)
    bins = stft.bin_freqs(FS, n_fft)
    mask = np.zeros(len(bins), dtype=bool)
    for k in HARMONICS:
        mask |= np.abs(bins - f0 * k) <= width_hz
    S[mask, :] = 0.0
    return stft.synthesize(S, meta)


def report(name, clean, noisy, out, f0):
    per = []
    for k in HARMONICS:
        g = band_gain_db(noisy, out, FS, k * f0 - 5.0, k * f0 + 5.0)
        per.append(None if g is None else round(g, 1))
    return dict(
        name=name,
        sdr_before=_r(sdr_db(noisy, clean)), sdr_after=_r(sdr_db(out, clean)),
        hum_suppression_db=per, energy=_r(energy_ratio_db(out, noisy)),
        peak=round(float(np.abs(out).max()), 4),
    )


def _r(v):
    return None if v is None else round(float(v), 2)


MATERIALS = {
    "带限白噪": corpus.band_limited_noise(SECONDS, FS, 18000.0, amp=0.5),
    "谐波音": corpus.harmonic_tone(SECONDS, FS, 440.0, 40, amp=0.5),
}

for F0 in (50.0, 60.0):
    for mat, clean in MATERIALS.items():
        for rel in (-20.0, -30.0):
            noisy = hum(clean, F0, rel)
            print("\n=== f0=%.0f Hz ｜ %s ｜ 嗡 %.0f dB ===" % (F0, mat, rel))
            rows = []
            for q in (10.0, 20.0, 40.0):
                rows.append(report("因果 Q=%.0f" % q, clean, noisy, causal(noisy, F0, q), F0))
            for q in (10.0, 20.0, 40.0):
                rows.append(report("零相位 Q=%.0f" % q, clean, noisy, zero_phase(noisy, F0, q), F0))
            for w in (6.0, 12.0, 24.0):
                t0 = time.perf_counter()
                o = stft_zero(noisy, F0, width_hz=w)
                r = report("STFT 置零 ±%.0f Hz" % w, clean, noisy, o, F0)
                r["ms"] = round((time.perf_counter() - t0) * 1000.0, 1)
                rows.append(r)
            print(" %-16s %8s %8s %-20s %7s" % ("候选", "SDR前", "SDR后", "谐波压制 dB", "能量比"))
            for r in rows:
                print(" %-16s %8s %8s %-20s %7s %s"
                      % (r["name"], r["sdr_before"], r["sdr_after"],
                         "/".join("—" if v is None else "%.1f" % v for v in r["hum_suppression_db"]),
                         r["energy"], r.get("ms", "")))

# ---------------------------------------------------------------- 电网频率漂移
#: 为什么单列这一段：陷波的 Q 决定**带宽**（50 Hz 处分 −3 dB 宽 = f0/Q）。
#: 电网不是理想 50.000 Hz：IEC 60038 的稳态容差是 ±0.5%（50 Hz ⇒ ±0.25 Hz），
#: 老录音转档还有走带偏速。若 Q 太大、陷波太窄，**漂了 0.5 Hz 的嗡就漏掉**——
#: 这比"多切一点内容"严重得多（漏掉的嗡是持续的、可听的）。
#: 判据：在 ±0.5 Hz 漂移下，某个 Q 是否还能把 1 次谐波压到 ≥ 20 dB。
print("\n=== 频率漂移鲁棒性（带限白噪，嗡 −20 dB，1 次谐波压制 dB）===")
clean = MATERIALS["带限白噪"]
print(" %-10s %s" % ("候选", "  ".join("%+5.1fHz" % d for d in (0.0, 0.25, 0.5, 1.0))))
for q in (10.0, 20.0, 30.0, 40.0):
    vals = []
    for det in (0.0, 0.25, 0.5, 1.0):
        noisy = hum(clean, 50.0 + det, -20.0)
        out = zero_phase(noisy, 50.0, q)          # 滤波器按标称 50 Hz 设计
        g = band_gain_db(noisy, out, FS, 45.0, 55.0)
        vals.append("—" if g is None else "%5.1f" % g)
    print(" 零相位 Q=%-3.0f %s" % (q, "  ".join(vals)))
for q in (10.0, 20.0, 30.0, 40.0):
    vals = []
    for det in (0.0, 0.25, 0.5, 1.0):
        noisy = hum(clean, 50.0 + det, -20.0)
        out = causal(noisy, 50.0, q)
        g = band_gain_db(noisy, out, FS, 45.0, 55.0)
        vals.append("—" if g is None else "%5.1f" % g)
    print(" 因果   Q=%-3.0f %s" % (q, "  ".join(vals)))
