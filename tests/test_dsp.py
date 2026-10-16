"""DSP 层：BS.1770 响度、真峰值、谱特征、立体声。"""
from __future__ import annotations

import numpy as np

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


# ------------------------------------------------- B14 自研：局部化反相检测
def _tone(fs, f, n):
    t = np.arange(n) / fs
    return (0.25 * np.sin(2 * np.pi * f * t)).astype(np.float32)


def test_anti_phase_scan_clean_stereo_is_not_flagged():
    """★ 这条是**指标口径的纠错回归**：正常立体声不许被报成反相。

    实测教训：第一版用**格数占比**，真立体声（L=1k / R=3k，完全正常）报 **18%**——
    那些格是泄漏裙边（一侧只有噪声、另一侧有信号，失衡不足 30 dB 所以 ρ 被保留）。
    改用**能量占比**后是 **0.02%**。格数会说谎，能量不会。
    """
    fs = 44100
    n = fs * 4
    r = dsp.anti_phase_scan(_tone(fs, 1000, n), _tone(fs, 3000, n), fs)
    assert r["anti_energy_pct"] < 1.0, "真立体声被报成反相（能量 %.3f%%）" % r["anti_energy_pct"]
    assert r["anti_scope"] == "无"


def test_anti_phase_scan_inphase_is_clean():
    fs = 44100
    n = fs * 4
    x = _tone(fs, 1000, n)
    r = dsp.anti_phase_scan(x, x, fs)
    assert r["anti_energy_pct"] == 0.0
    assert r["anti_scope"] == "无"


def test_anti_phase_scan_full_inversion_is_severe():
    fs = 44100
    n = fs * 4
    x = _tone(fs, 1000, n)
    r = dsp.anti_phase_scan(x, -x, fs)
    assert r["anti_energy_pct"] > 90.0
    assert r["anti_scope"] == "严重"


def test_anti_phase_scan_locates_the_band():
    """**这条是它存在的理由**：全局 corr 看不见"只在某频段反相"，它能。

    L = 1k+3k、R = 1k−3k ⇒ 1 kHz 同相、3 kHz 反相。全局 corr = 0
    （两段能量相抵），而真实情况是"一半内容反相"。
    """
    fs = 44100
    n = fs * 4
    L = _tone(fs, 1000, n) + _tone(fs, 3000, n)
    R = _tone(fs, 1000, n) - _tone(fs, 3000, n)
    r = dsp.anti_phase_scan(L, R, fs)
    assert r["anti_energy_pct"] > 30.0, "局部反相没被检出"
    assert r["anti_band_khz"] is not None and 2.5 < r["anti_band_khz"] < 3.5, \
        "反相集中频段报成了 %s（应为 ~3 kHz）" % r["anti_band_khz"]


def test_anti_phase_scan_uncorrelated_stereo_is_not_flagged():
    """★ **口径纠错回归（第二次）**：左右**不相关**的正常立体声不许被判"反相"。

    实测教训：单帧 ρ 对不相关内容是**重尾**的——左右完全独立的正常立体声
    有 **33%** 的能量落在 ρ<−0.5（−0.9 时 14%、−0.98 时仍 6.3%），
    于是 GUI 把正常立体声标成"反相范围：明显"，而**真缺陷**只报 0.76% 被漏掉。
    改用**跨帧相干** ρ̄ 后：独立立体声 0.07%、全带反相 100%（实测）。
    """
    fs = 44100
    n = fs * 3
    rng = np.random.default_rng(20261016)
    left = rng.standard_normal(n).astype(np.float32)
    right = rng.standard_normal(n).astype(np.float32)
    r = dsp.anti_phase_scan(left, right, fs)
    assert r["anti_energy_pct"] < 1.0, \
        "左右独立的正常立体声被报成反相（%.3f%%）——又退回单帧 ρ 了？" % r["anti_energy_pct"]
    assert r["anti_scope"] == "无"


def test_anti_phase_scan_correlated_stereo_is_clean():
    """相关 0.5 + 各自细节的"真音乐样"也不许被报成反相。"""
    fs = 44100
    n = fs * 3
    rng = np.random.default_rng(7)
    common = rng.standard_normal(n).astype(np.float32)
    left = common
    right = (0.5 * common + 0.8 * rng.standard_normal(n)).astype(np.float32)
    r = dsp.anti_phase_scan(left, right, fs)
    assert r["anti_energy_pct"] < 1.0, "真音乐样被报成反相（%.3f%%）" % r["anti_energy_pct"]
    assert r["anti_scope"] == "无"


def test_anti_phase_coherence_window_is_positive():
    """相干估计的时间窗必须 > 0（为 0 就退化成单帧 ρ，见上一条的教训）。"""
    assert dsp.ANTI_COH_SEC > 0.0
    assert dsp.ANTI_RHO < 0.0


def test_anti_phase_scan_short_input_is_empty():
    assert dsp.anti_phase_scan(np.zeros(10, "float32"), np.zeros(10, "float32"), 44100) == {}


def test_phase_field_matches_anti_phase_scan_implementation():
    """**一处实现**：视图层与判定层必须调同一个函数（不许有两份数学）。"""
    import inspect

    from audiocheck.core import views
    src = inspect.getsource(views.stereo_field)
    assert "dsp.phase_field" in src, "views.stereo_field 没有复用 dsp.phase_field"
    assert "signal.stft" not in src, "views.stereo_field 里又写了一份 STFT"
