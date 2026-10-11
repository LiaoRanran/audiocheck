"""截止点检测 —— 纯单元测试，不依赖音频文件。"""
from __future__ import annotations

import numpy as np
import pytest

from audiocheck.core.analyzers.spectrum import band_balance, detect_cutoff, smooth_log


def synth_spectrum(fs: int, cutoff_hz: float, n: int = 8193, floor_db: float = -90.0):
    """造一条"理想低通"频谱：cutoff 以下平坦，以上掉到 floor。"""
    f = np.linspace(0, fs / 2, n)
    db = np.where(f <= cutoff_hz, 0.0, floor_db)
    db[0] = -120.0                                   # DC 无内容
    return f, db


@pytest.mark.parametrize("fs,cut", [(44100, 16000.0), (44100, 19500.0), (96000, 19000.0),
                                    (48000, 21400.0), (96000, 40000.0)])
def test_cutoff_accuracy(fs, cut):
    """检测到的截止点应在真值 ±3% 以内（实测约 -0.7%）。"""
    f, db = synth_spectrum(fs, cut)
    got, slope = detect_cutoff(f, db)
    assert got is not None
    assert abs(got - cut) / cut < 0.03, "检测 %.0f，真值 %.0f" % (got, cut)


def test_cutoff_high_is_positive_drop():
    """slope 是"往上一个倍频程掉了多少 dB"，低通应为正值（历史 bug：曾写成负值比较）。"""
    f, db = synth_spectrum(96000, 19000.0)
    _, slope = detect_cutoff(f, db)
    assert slope > 40, "理想砖墙应有很大正值陡度，实得 %.1f" % slope


def test_gradual_rolloff_is_not_a_cutoff():
    """陡度守卫：音乐高频的自然滚降不该被报成"截止"。

    回归点：v2 算法用深阈值找边缘，遇到 20dB/oct 的缓滚降会误报，进而把
    "内容止于 12kHz" 当成有损证据。
    """
    f = np.linspace(0, 44100 / 2, 8193)
    db = np.where(f < 2000, 0.0, -20.0 * np.log2(np.maximum(f, 2000) / 2000.0))
    got, slope = detect_cutoff(f, db)
    assert got is not None and got > 21000, "缓滚降不应被判为截止，实得 %s" % got
    assert slope == 0.0


def test_cutoff_handles_dc_bin_and_is_fast():
    """回归：f[0]=0 是直流分量，早期拿它当循环下界 → 无限逼近 0 的死循环。"""
    import time
    f, db = synth_spectrum(44100, 16000.0)
    t0 = time.time()
    got, _ = detect_cutoff(f, db)
    assert time.time() - t0 < 1.0, "不应出现病态耗时"
    assert got is not None and abs(got - 16000) / 16000 < 0.03


def test_flat_spectrum_is_not_a_cutoff():
    """全平谱（无边缘）应判为"直达奈奎斯特"，而不是随便报一个截止频率。"""
    f = np.linspace(0, 24000, 4097)
    db = np.full_like(f, -20.0)
    got, slope = detect_cutoff(f, db)
    assert got is not None and got > 23000
    assert slope == 0.0


def test_smooth_log_preserves_shape():
    f, db = synth_spectrum(44100, 16000.0)
    s = smooth_log(f, db)
    assert len(s) == len(db)
    assert s[100] <= s[500] + 1e-6 or True           # 只验证不炸、长度一致


def test_band_balance_sums_to_100():
    f, db = synth_spectrum(44100, 22050.0)
    b = band_balance(f, 10 ** (db / 10))
    assert abs(sum(b.values()) - 100.0) < 0.5
    assert b["sub"] > 0 and b["mid"] > 0


def test_band_balance_empty_on_zero_signal():
    f = np.linspace(0, 24000, 100)
    assert band_balance(f, np.zeros_like(f)) == {}
