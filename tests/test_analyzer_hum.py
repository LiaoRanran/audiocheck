"""嗡自动检测的判据：**稳态**把嗡与乐音分开（不是谐波族）。

真值来自合成语料：嗡是我们叠上去的（频率/电平已知），乐音对照是衰减贝斯音
（55/41.2/82.4 Hz，含 6 次谐波 + 包络 + 揉弦）。三个门限都落在实测空隙里
（见 `core/analyzers/hum.py` 的 docstring 表）。
"""
from __future__ import annotations

import numpy as np
import pytest

from audiocheck.core.analyzers.hum import detect_hum
from audiocheck.core.edit.corpus import band_limited_noise

FS = 48000


def _hum(clean: np.ndarray, f0: float, rel_db: float, fs: int = FS, seed: int = 7) -> np.ndarray:
    n = clean.shape[0]
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    h = np.zeros(n)
    for k in (1, 2, 3):
        h += np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 2 * np.pi)) / k
    h *= (10 ** (rel_db / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    return clean + h[:, None]


def _bass(f0: float, fs: int = FS, seconds: float = 6.0, decay_s: float = 0.35,
          amp: float = 0.5, seed: int = 3) -> np.ndarray:
    """衰减贝斯音：6 次谐波 + 指数包络 + 揉弦；每 1.5 s 重新起音（有"音符"在弹）。"""
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
    gate = (np.floor(t / 1.5) % 2 == 0).astype(float)
    gate = np.convolve(gate, np.hanning(256) / np.hanning(256).sum(), mode="same")
    return (x * gate)[:, None]


def test_detects_mains_hum_on_noise():
    """带限白噪 + 50 Hz 嗡（−20 dB）⇒ 检出 f0=50（±0.2），三项证据都在门限之上。"""
    x = _hum(band_limited_noise(6.0, FS, 18000.0, amp=0.5), 50.0, -20.0)
    r = detect_hum(x, FS)
    assert r["accepted"] is True
    assert abs(r["f0_hz"] - 50.0) <= 0.2, "检出 %s" % r["f0_hz"]
    assert min(r["excess_db"]) >= 4.0
    assert max(r["stability_db"]) <= 30.0
    assert min(r["quiet_excess_db"]) >= 0.0


def test_detects_60hz_hum_at_minus_30db():
    """60 Hz、−30 dB（弱嗡）也要检出——这是实测表里最不利的一档。"""
    x = _hum(band_limited_noise(6.0, FS, 18000.0, amp=0.5), 60.0, -30.0)
    r = detect_hum(x, FS)
    assert r["accepted"] is True
    assert abs(r["f0_hz"] - 60.0) <= 0.2, "检出 %s" % r["f0_hz"]


def test_refuses_clean_noise():
    """无嗡素材 ⇒ 必须拒绝（白噪的 excess 只有 ~4 dB，低于 4 的门限）。"""
    r = detect_hum(band_limited_noise(6.0, FS, 18000.0, amp=0.5), FS)
    assert r["accepted"] is False
    assert "证据不足" in r["reason"]


@pytest.mark.parametrize("f0", [55.0, 41.2, 82.4])
def test_refuses_a_bass_note(f0):
    """★ **乐音对照**：55 Hz 贝斯的 excess 比嗡还高（34.7 dB），但它是**非稳态**的。

    帧间稳定性 500+ dB（有起音/衰减/静音），安静帧 excess 是负的 ⇒ 两项都把它挡掉。
    这条是"为什么不能只看谐波族"的判据。
    """
    r = detect_hum(_bass(f0), FS)
    assert r["accepted"] is False, "把 %.1f Hz 的贝斯当成了嗡（f0=%s）" % (f0, r["f0_hz"])
    assert max(r["stability_db"]) > 100.0


def test_detects_hum_under_a_bass_note():
    """贝斯在弹、嗡也在：判定取**稳态线**（50 Hz），不是贝斯的基频。"""
    r = detect_hum(_hum(_bass(55.0), 50.0, -30.0), FS)
    assert r["accepted"] is True
    assert abs(r["f0_hz"] - 50.0) <= 0.3, "检出 %s" % r["f0_hz"]


def test_short_or_unsupported_material_is_refused():
    """太短 / 采样率放不下 3 次谐波 ⇒ 拒绝并说明原因（不猜）。"""
    short = detect_hum(band_limited_noise(1.0, FS, 18000.0), FS)
    assert short["accepted"] is False and "太短" in short["reason"]
    low_fs = detect_hum(band_limited_noise(3.0, 300, 100.0), 300)
    assert low_fs["accepted"] is False and "谐波" in low_fs["reason"]
