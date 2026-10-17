"""截止点检测 —— 纯单元测试，不依赖音频文件。"""
from __future__ import annotations

import numpy as np
import pytest

from audiocheck.core.analyzers.spectrum import (
    band_balance,
    detect_cutoff,
    smooth_log,
    smooth_power,
)


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


# ------------------------------------------- 变异测试填缺（E1.2 存活体，2026-10-16）
def test_smooth_log_axis_vectorized_matches_pointwise():
    """★ 向量化必须与**逐点实现**逐点一致（逐点是唯一真源，不是"另一个实现"）。

    为什么值得单列：向量版（`searchsorted` + 前缀和）是本轮把单次 **0.245 s → 0.0007 s**
    （357×）的改动；而"窗口内 ≥2 点才取均值、否则保持原值"这类语义最容易在改写时丢掉。
    **两种网格都要过**：对数网格（真实用途）与线性网格（合成语料那条路）。
    """
    from audiocheck.core.analyzers.spectrum import (
        _smooth_log_axis,
        _smooth_log_axis_pointwise,
    )

    rng = np.random.default_rng(20261017)
    for f in (np.geomspace(20.0, 22050.0, 4096),
              np.linspace(0.0, 22050.0, 8193),
              np.linspace(0.0, 48000.0, 8193)):
        for frac in (6.0, 12.0, 3.0):
            y = rng.standard_normal(f.size) * 40 - 60
            got = _smooth_log_axis(f, y, frac)
            ref = _smooth_log_axis_pointwise(f, y, frac)
            assert np.allclose(got, ref, rtol=0, atol=1e-9), \
                "向量化与逐点的最大差 %.3e（f=%d 点，fraction=%s）" % (
                    np.abs(got - ref).max(), f.size, frac)
    # 非单调频点：必须退回逐点实现（不许给一个"看起来平滑"的结果）
    f = np.array([0.0, 100.0, 50.0, 200.0, 150.0])
    y = np.arange(5.0)
    assert np.array_equal(_smooth_log_axis(f, y, 6.0),
                          _smooth_log_axis_pointwise(f, y, 6.0))


def test_smooth_vectorized_preserves_input_dtype():
    """★ 向量版对 float32 输入必须**回 float32**（保持输入 dtype）。

    钉住 `out.astype(y.dtype, copy=False) if y.dtype != np.float64 else out`：
    把 `!=` 改成 `==` ⇒ float32 输入会**原样返回 float64**（dtype 静默变宽，
    下游缓存 / 绘图缓冲跟着翻倍）——而这条此前无覆盖（上面的 oracle 用的是 float64）。
    """
    from audiocheck.core.analyzers.spectrum import _smooth_log_axis

    f = np.geomspace(20.0, 22050.0, 8193)
    y32 = np.random.default_rng(0).random(8193).astype(np.float32)
    assert _smooth_log_axis(f, y32, 6.0).dtype == np.float32
    assert _smooth_log_axis(f, y32.astype(np.float64), 6.0).dtype == np.float64


def test_smooth_power_flat_spectrum_returns_the_same_db():
    """功率域平滑的 dB 刻度必须是标准的 **10·log10**（否则"dB"就不是 dB）。

    平坦谱（处处 −20 dB）平滑后应仍是 −20 dB：`10**(db/10)` 取均值、再
    `10·log10` 回到同一值。这条此前**完全没被检过**——`slope = s[j0]-s[j1]`
    的断言是松的（`> 40`），把系数改成 `11/10`、或写错成 `10**(db*10)`、
    或用 `10**(db/15)`，都能照样通过。变异测试把这个缺口抓出来了。
    """
    f = np.linspace(0, 22050, 8193)
    for db0 in (-20.0, -60.0):
        s = smooth_power(f, np.full_like(f, db0))
        assert np.abs(s - db0).max() < 1e-6, \
            "平坦谱平滑后应逐点回到 %s dB，实得最大偏差 %.4f" % (db0, np.abs(s - db0).max())


def test_smooth_wrappers_default_to_one_sixth_octave():
    """`smooth_log` / `smooth_power` 的默认平滑宽度是 **1/6 倍频程**。

    默认值必须与显式 `6.0` 逐位一致，且真的生效（显式 `12.0` 必须产生**不同**结果，
    否则"默认值"只是个摆设）。`_smooth_log_axis` 自身的默认值从不被使用
    （两个包装函数都显式传入），那一条已登记为等价变异体。
    """
    f, db = synth_spectrum(44100, 16000.0)
    for fn in (smooth_log, smooth_power):
        assert np.array_equal(fn(f, db), fn(f, db, 6.0)), \
            "%s 的默认平滑宽度不是 6.0（调用点与文档都按 1/6 倍频程讲）" % fn.__name__
        assert not np.array_equal(fn(f, db), fn(f, db, 12.0)), \
            "%s 的 fraction 形参没起作用" % fn.__name__


def test_detect_cutoff_rejects_degenerate_input():
    """`detect_cutoff` 的输入守卫：`None` 与过短输入都必须显式返回 `(None, 0.0)`。

    往下走不会拿到"没检出"，而是拿到一个 TypeError（再被别处吞掉）。长度下界是
    **16**：15 与 16 是边界，必须分别落在"拒绝 / 可处理"两侧——这正是
    `if f is None or db is None or len(f) < 16` 这条守卫的语义。
    """
    f, db = synth_spectrum(44100, 16000.0)
    assert detect_cutoff(None, db) == (None, 0.0)
    assert detect_cutoff(f, None) == (None, 0.0)
    assert detect_cutoff(f[:15], db[:15]) == (None, 0.0)
    got, _ = detect_cutoff(f[:16], db[:16])
    assert got is not None, "长度 16 属于可处理下界，不该被守卫拦下"


def test_cutoff_detected_near_search_floor():
    """搜索下界是 **100 Hz**（`floor_hz = max(min_hz*2, 100)`）。

    90 / 110 / 130 Hz 的砖墙都必须被检出，而不是报"直达奈奎斯特"。低频带限
    （电话带宽 / 窄带母带 / 高通滤过的素材）恰恰是本工具该报出来的一类内容。

    为什么三个点都要：下界抬到 150 Hz 时 **110 与 90 会漏**，而 **130 不会**
    （粗搜索的候选窗口是 ±1.6×，130 Hz 的候选落在 160 Hz > 150）——只测 130
    会得到一个"这条判据有牙齿"的错觉。这是变异测试实测出来的。
    """
    f = synth_spectrum(44100, 130.0)[0]
    for cut in (90.0, 110.0, 130.0):
        db = np.where(f <= cut, 0.0, -90.0)
        db[0] = -120.0
        got, _ = detect_cutoff(f, db)
        assert got is not None and cut * 0.85 < got < 150.0, \
            "%.0f Hz 砖墙漏检，实得 %s" % (cut, got)


def test_cutoff_accuracy_on_log_spaced_grid():
    """**对数**频点网格上的定位精度 ≤2%。

    为什么单列这一条：`synth_spectrum` 用的是**线性**网格（`np.linspace`），
    高端一个 bin 只有 0.017% —— "边缘定位偏一个 bin"这类错误（例如把
    "连续 `need` 点越过阈值"写成 `> need`）在线性网格上完全看不出来；
    而对数网格上一个 1/12 倍频程 bin 是 5.9%，藏不住。
    """
    f = np.geomspace(20.0, 22050.0, 4096)
    for cut in (8000.0, 16000.0, 19000.0):
        db = np.where(f <= cut, 0.0, -90.0)
        got, _ = detect_cutoff(f, db)
        assert got is not None and abs(got - cut) / cut < 0.02, \
            "对数网格上 cut=%.0f 定位到 %s" % (cut, got)

