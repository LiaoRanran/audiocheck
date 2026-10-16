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
    """相干估计的时间窗必须 **> 0**（为 0 就退化成单帧 ρ，见上一条的教训），
    且**恰好 0.5 s**——docstring 里写明这是"太短消不掉随机符号、太长会把只反相
    一小段抹平"之间实测出来的折中。改它必须是一次有意识的、会动读数的决定。
    """
    assert dsp.ANTI_COH_SEC == 0.5
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


# ------------------------------------------- 变异测试填缺（E1.2 存活体，2026-10-16）
def test_phase_field_is_actually_exercised():
    """`phase_field` 是视图层与判定层共用的**唯一**实现——此前只有一条
    "源码里复用了它"的断言，**从没真的调用过它**（缺一个声道时内部会 TypeError）。

    同时钉住 **STFT 窗口径**：默认 `nperseg=4096` ⇒ fs=44100 下频点间距必须是
    fs/4096（2049 个频点）。4096 与 4097 的频点数**都是 2049**，所以只有间距
    能把它区分开。全带反相时 ρ 应接近 −1（这是 ρ 的定义本身）。
    """
    import inspect

    fs = 44100
    n = fs
    t = np.arange(n) / fs
    x = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    f, _tt, rho = dsp.phase_field(np.column_stack([x, -x]), fs)
    assert len(f) == 2049
    assert abs((f[1] - f[0]) - fs / 4096.0) < 1e-9, "默认 STFT 窗不是 4096 点"
    band = (f > 900.0) & (f < 1100.0)
    assert np.all(rho[band, :] < -0.9), "全带反相时 ρ 应接近 −1"
    # 两个入口的窗口径必须同一个（视图层与判定层共用一套数学）
    for fn in (dsp.phase_field, dsp.anti_phase_scan):
        assert inspect.signature(fn).parameters["nperseg"].default == 4096, \
            "%s 的默认窗口径漂了（频谱图/相位场必须同一个 4096）" % fn.__name__


def test_phase_field_marks_weak_side_undefined():
    """某格上**弱侧比强侧低过 `PHASE_UNDEF_DB`（30 dB）时 ρ 无定义**，必须置 0。

    ρ = Re(L·conj(R)) / (|L|·|R|)：若某频点上只有一个声道有能量，分母趋近 0，
    剩下的是数值噪声（实测会被推到 ±1.00）⇒ 会报出"这里完全反相"的假结论。
    门限放宽到 45 dB，这个 −35 dB 的格就会被当成"有定义"而生成假反相。
    """
    fs = 44100
    t = np.arange(fs) / fs
    left = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    right = (0.5 * 10 ** (-35 / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    f, _tt, rho = dsp.phase_field(np.column_stack([left, right]), fs)
    band = (f > 900.0) & (f < 1100.0)
    assert np.all(rho[band, :] == 0.0), \
        "弱侧低 35 dB（超过 30 dB 判据）的格应判『ρ 无定义』并置 0"


def test_anti_phase_scan_ignores_below_floor_energy():
    """`ANTI_FLOOR_DB`（40 dB）：只统计高于本帧峰值 40 dB 以内的格。

    构造：主内容同相（响），另有一个**低 45 dB 的反相音**。低 45 dB 的格落在
    40 dB 门限之外 ⇒ 不该计入反相能量（读数应是"无"，`anti_energy_db` 远低于 −100）。
    门限放宽到 60 dB 就会把它算进来，读数从"无"跳到约 −45 dB。
    """
    fs = 44100
    n = fs * 3
    t = np.arange(n) / fs
    main = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    quiet_rev = (0.5 * 10 ** (-45 / 20) * np.sin(2 * np.pi * 6000 * t)).astype(np.float32)
    r = dsp.anti_phase_scan(main + quiet_rev, main - quiet_rev, fs)
    assert r["anti_energy_db"] < -100.0, \
        "低于 40 dB 门限的反相内容不该计入（实得 %s dB）" % r["anti_energy_db"]
    assert r["anti_scope"] == "无"


def test_spectral_features_empty_on_zero_power():
    """全零功率谱（整段静音的 STFT）应返回 `{}`，不是一堆 nan。

    `tot <= 0` 是这类输入的守卫；改成 `<` 会放行 ⇒ 质心/滚降全变 nan
    还会污染下游判定。
    """
    f = np.linspace(0, 22050, 8193)
    assert dsp.spectral_features(f, np.full_like(f, -4000.0)) == {}


def test_stereo_features_rejects_missing_channel():
    """缺一个声道时必须显式返回 `{}`（`left is None or right is None` 的守卫）。"""
    x = np.zeros(2048)
    assert dsp.stereo_features(x, None) == {}
    assert dsp.stereo_features(None, x) == {}


def test_stereo_features_minimum_length_is_1024():
    """最短 1024 采样（`lc.size < 1024` 的边界）：1024 可处理，1023 拒绝。"""
    x = np.random.default_rng(3).standard_normal(1024)
    assert "corr" in dsp.stereo_features(x, x)
    assert dsp.stereo_features(x[:1023], x[:1023]) == {}


def test_loudness_rejects_degenerate_sample_rate():
    """`hop = int(0.1·fs)` 取整为 0（fs<10）时必须直接返回 `{}`，不许往下走。

    守卫是 `if hop < 1 or total < hop*4: return {}`；放行会在
    `nsteps = total // hop` 处 ZeroDivisionError。
    """
    assert dsp.loudness("does-not-matter.wav", 4, 2, 1000) == {}


def test_short_term_extremes_are_on_the_loudness_scale(audio):
    """LRA 的短时块核必须是**均值**（`np.ones(w)/w`）。

    写成 `*w`（各元素 0.1 ⇒ 合计 0.3）会把短时响度整体压低 10·log10(0.3) ≈ −5.2 dB。
    LRA 是**极差**、对常数平移不敏感（所以 `lra` 抓不到），只有 `st_max` / `st_min`
    能抓——稳态正弦的短时极大值应与整轨响度一致。
    """
    r = dsp.loudness(audio["tone_50"], 48000, 2, int(48000 * 6))
    assert r["st_max"] is not None and r["lufs"] is not None
    assert abs(r["st_max"] - r["lufs"]) < 0.5, \
        "稳态正弦的 st_max 应与整轨响度一致（实得 %s vs %s）" % (r["st_max"], r["lufs"])

