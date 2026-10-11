"""DSP 层：BS.1770 响度、真峰值、谱特征、立体声。"""
from __future__ import annotations

import numpy as np
import pytest

from audiocheck.core import dsp


def test_k_weighting_shapes(fs=48000):
    (b1, a1), (b2, a2) = dsp.k_weighting(fs)
    for arr, n in ((b1, 3), (a1, 3), (b2, 3), (a2, 3)):
        assert len(arr) == n
    assert a1[0] == 1.0 and a2[0] == 1.0


def test_loudness_scale_invariance(tmp_path, audio):
    """幅度减半（-6.02 dB）后集成响度应下降约 6 LU —— 验证的是标准实现而非绝对值。"""
    a = dsp.loudness(audio["tone_50"], 48000, 2, int(48000 * 6))
    b = dsp.loudness(audio["tone_25"], 48000, 2, int(48000 * 6))
    assert a["lufs"] is not None and b["lufs"] is not None
    assert abs((a["lufs"] - b["lufs"]) - 6.02) < 0.3, \
        "响度差应约 6.02 LU，实得 %.2f" % (a["lufs"] - b["lufs"])


def test_loudness_of_full_scale_sine_is_plausible(audio):
    """0.5 幅度正弦的集成响度应在 -9 ~ -5 LUFS 之间（BS.1770 对 1kHz 增益略大于 0dB）。"""
    r = dsp.loudness(audio["tone_50"], 48000, 2, int(48000 * 6))
    assert -9.5 < r["lufs"] < -4.5, "实得 %s" % r["lufs"]


def test_true_peak_near_sample_peak_for_sine(audio):
    """正弦的真峰值应接近其采样峰值（0.5 → -6.02 dBFS），不会离谱地高。"""
    r = dsp.loudness(audio["tone_50"], 48000, 2, int(48000 * 6))
    assert abs(r["true_peak_dbfs"] - (-6.02)) < 0.6, "实得 %s" % r["true_peak_dbfs"]


def test_plr_equals_true_peak_minus_lufs(audio):
    r = dsp.loudness(audio["tone_50"], 48000, 2, int(48000 * 6))
    assert abs(r["plr"] - (r["true_peak_dbfs"] - r["lufs"])) < 0.11


def test_spectral_features_on_white_noise():
    f = np.linspace(0, 22050, 8193)
    db = np.full_like(f, -20.0)
    db[0] = -100.0
    feats = dsp.spectral_features(f, db)
    assert 5000 < feats["centroid_hz"] < 16000, "白噪声重心应在中高频，实得 %s" % feats["centroid_hz"]
    assert feats["rolloff85_hz"] > 15000
    assert feats["flatness"] > 0.5, "白噪声谱平坦度应接近 1"


def test_spectral_features_on_lowpassed():
    f = np.linspace(0, 22050, 8193)
    db = np.where(f < 500, -20.0, -120.0)
    feats = dsp.spectral_features(f, db)
    assert feats["centroid_hz"] < 1500, "窄带低频信号重心应很低，实得 %s" % feats["centroid_hz"]


def test_stereo_features_identical_channels():
    x = np.random.default_rng(1).standard_normal(100000).astype(np.float64)
    feats = dsp.stereo_features(x, x)
    assert abs(feats["corr"] - 1.0) < 0.01
    assert feats["mono_compat"] == "良好"


def test_stereo_features_inverted_channel():
    x = np.random.default_rng(2).standard_normal(100000).astype(np.float64)
    feats = dsp.stereo_features(x, -x)
    assert feats["corr"] < -0.99
    assert "单声道兼容性差" in feats["mono_compat"]
