"""编辑侧测量层的判据（`core/edit/metrics.py`）。

这一层的规矩与判定层不同：**只出数，不出"好/坏"**（门限属于上层）。
所以每条判据都是"**答案本来就知道**"的构造——不是"看起来合理"：

* 带内衰减：把已知倍率/已知掩码作用上去，量出来的 dB 必须**回到**那个数；
* 包络宽度：**纯缩放不许改变它**（改了就说明这把尺子在量电平不是量宽度）；
* SDR / 能量比：按定义构造误差能量占 1% ⇒ **必须**是 20.00 dB。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import render_band  # noqa: E402
from audiocheck.core.edit.metrics import (  # noqa: E402
    band_energy,
    band_gain_db,
    energy_ratio_db,
    envelope_width_ms,
    sdr_db,
)

FS = 48000
N_FFT = 4096


def _burst_at(seconds: float, f: float, width_ms: float, n: int = None) -> np.ndarray:
    """在 `seconds` 秒里放一个**升余弦包络**的 `f` 正弦脉冲（宽 `width_ms`）。"""
    n = int(seconds * FS) if n is None else n
    x = np.zeros(n)
    L = int(width_ms * FS / 1000.0)
    i0 = n // 2 - L // 2
    env = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(L) / L)      # 升余弦：两端为 0，无边
    t = np.arange(L) / FS
    x[i0:i0 + L] = env * np.sin(2 * np.pi * f * t)
    return x[:, None]


# ---------------------------------------------------------------- 带内衰减
def test_band_gain_db_recovers_a_known_scaling():
    """整段 ×0.1 ⇒ 任何频带的实测增益都必须是 −20.00 dB（比值是精确的）。"""
    x = _burst_at(0.5, 8000.0, 100.0)
    got = band_gain_db(x, x * 0.1, FS, 7000.0, 9000.0)
    assert got is not None and abs(got + 20.0) < 0.01, got


def test_band_gain_db_is_none_only_when_the_band_is_exactly_empty():
    """"空带"的判据是**精确 0**，不是"很小"。

    ⚠ 别把它改成"低于某阈值就当空"：float64 里"看起来空"的带仍有 −300 dB 量级的
    数值残留，那时**比值仍然有定义、而且是正确的**（比如整段缩放的比值恒为设定值）。
    把它当"没法量"反而是把信息藏起来——本仓不许这种"宁可不做"的偷懒用法，
    故只在**真 0**（分母为 0）时才返回 `None`。
    """
    z = np.zeros((24000, 1))
    assert band_gain_db(z, z, FS, 7000.0, 9000.0) is None
    # 1 kHz 的脉冲在 7–9 kHz 上是"数值空"（−300 dB 量级），但整段 ×0.1 的比值照样精确
    x = _burst_at(0.5, 1000.0, 100.0)
    got = band_gain_db(x, x * 0.1, FS, 7000.0, 9000.0)
    assert got is not None and abs(got + 20.0) < 0.01, got


def test_band_gain_db_measures_a_real_render(tmp_path):
    """**真渲染**上量：带内**单频**设 −20 dB ⇒ 实测必须回到 −20 dB（±0.1）。"""
    t = np.arange(FS) / FS
    x = (0.3 * np.sin(2 * np.pi * 8000 * t))[:, None]
    src = str(tmp_path / "src.wav")
    dst = str(tmp_path / "dst.wav")
    sf.write(src, x.astype(np.float32), FS, subtype="FLOAT")
    render_band(src, dst, 0.2, 0.8, 7000.0, 9000.0, -20.0, overwrite=True)
    a, _ = sf.read(src, dtype="float64", always_2d=True)
    b, _ = sf.read(dst, dtype="float64", always_2d=True)
    # 只取选区**内区**（两端各让开一个窗宽），避开边界过渡带
    k0, k1 = int(0.2 * FS) + N_FFT, int(0.8 * FS) - N_FFT
    got = band_gain_db(a, b, FS, 7000.0, 9000.0, k0, k1)
    assert got is not None and abs(got + 20.0) < 0.1, got


def test_band_gain_db_is_shallower_than_set_on_broadband_material(tmp_path):
    """⚠ **宽带素材上实测会比设定浅**（实测 −18.6 dB vs 设定 −20 dB）——这是真实现象，不是量具坏了。

    机制：掩码是在 STFT 域乘的，而**分析窗的主瓣/旁瓣会把带外内容搬进带内 bin**；
    那些"搬进来"的部分**不在掩码的带里**，于是没被压。
    ⇒ 输出信号在 7–9 kHz 上的**真实频谱** = 被压部分（−20 dB）+ 泄漏部分 ⇒ 实测浅。
    ⇒ 所以"实测衰减"这条数字必须**在同一个 STFT 表示里**量（审计字段
    `attenuation_measured_db` 就是这么做的），否则会把窗泄漏算成"编辑没做够"。
    这里把那个偏差**钉成一个区间**（回归口径，实测 1.4 dB 量级）。
    """
    rng = np.random.default_rng(17)
    x = rng.standard_normal((FS, 1)) * 0.05      # 宽带内容：整个 7–9 kHz 都有东西
    src = str(tmp_path / "src.wav")
    dst = str(tmp_path / "dst.wav")
    sf.write(src, x.astype(np.float32), FS, subtype="FLOAT")
    render_band(src, dst, 0.2, 0.8, 7000.0, 9000.0, -20.0, overwrite=True)
    a, _ = sf.read(src, dtype="float64", always_2d=True)
    b, _ = sf.read(dst, dtype="float64", always_2d=True)
    k0, k1 = int(0.2 * FS) + N_FFT, int(0.8 * FS) - N_FFT
    got = band_gain_db(a, b, FS, 7000.0, 9000.0, k0, k1)
    assert got is not None
    assert -20.0 < got < -18.0, (
        "宽带素材的实测衰减 %.2f dB：应当**浅于**设定（泄漏）但偏差是 dB 量级" % got)
    # 带外（3–5 kHz）**有真实内容**，这里才量得出"没被碰"：实测应为 0
    out = band_gain_db(a, b, FS, 3000.0, 5000.0, k0, k1)
    assert out is not None and abs(out) < 0.05, out


def test_band_energy_needs_both_or_neither_end():
    x = np.zeros((1000, 1))
    with pytest.raises(ValueError):
        band_energy(x, FS, 100.0, 200.0, 10, None)


# ---------------------------------------------------------------- 包络宽度
def test_envelope_width_is_scale_invariant():
    """**纯缩放不许改变宽度**：这把尺子量的是宽度，不是电平。"""
    x = _burst_at(0.5, 8000.0, 5.0)
    w0 = envelope_width_ms(x, FS)
    w1 = envelope_width_ms(x * 0.02, FS)          # 掉 34 dB
    assert w0 is not None and w1 is not None
    assert abs(w1 - w0) < 1e-9, (w0, w1)


def test_envelope_width_tracks_burst_length():
    """5 ms 与 15 ms 的脉冲，宽度应当接近 3:1（同一把尺子上的比例关系）。"""
    w5 = envelope_width_ms(_burst_at(0.5, 8000.0, 5.0), FS)
    w15 = envelope_width_ms(_burst_at(0.5, 8000.0, 15.0), FS)
    assert w5 is not None and w15 is not None
    assert 2.5 < w15 / w5 < 3.5, (w5, w15)


def test_envelope_width_grows_when_a_transient_is_smeared(tmp_path):
    """**真渲染**上的涂改：选区内一个 8 kHz 短脉冲被压掉后，残包络**变宽**。

    这是 `packaging/mask_tradeoff.py` 要量的那件事的判据形态：时变增益把能量
    在时间上摊开 ⇒ 脉冲的包络宽度增大。数字化 = "涂改了多少 ms"。
    """
    x = _burst_at(0.6, 8000.0, 5.0)
    src = str(tmp_path / "src.wav")
    dst = str(tmp_path / "dst.wav")
    sf.write(src, x.astype(np.float32), FS, subtype="FLOAT")
    render_band(src, dst, 0.3, 0.9, 7000.0, 9000.0, -40.0, overwrite=True)
    a, _ = sf.read(src, dtype="float64", always_2d=True)
    b, _ = sf.read(dst, dtype="float64", always_2d=True)
    k0, k1 = int(0.3 * FS), int(0.9 * FS)
    wa, wb = envelope_width_ms(a, FS, k0, k1), envelope_width_ms(b, FS, k0, k1)
    assert wa is not None and wb is not None
    assert wb >= wa, "被压之后的残包络不该比源更窄：源 %.3f ms / 后 %.3f ms" % (wa, wb)


# ---------------------------------------------------------------- SDR / 能量比
def test_sdr_db_is_exactly_20_when_error_energy_is_1_percent():
    """按定义构造：误差能量恰为参考的 1% ⇒ **20.00 dB**（不是"差不多"）。"""
    rng = np.random.default_rng(3)
    ref = rng.standard_normal((4096, 1))
    ref /= math.sqrt(float(np.sum(ref ** 2)))            # 归一化到单位能量
    err = rng.standard_normal((4096, 1))
    err /= math.sqrt(float(np.sum(err ** 2))) * 10.0     # 能量 = 1%
    got = sdr_db(ref + err, ref)
    assert got is not None and abs(got - 20.0) < 1e-9, got


def test_sdr_and_energy_ratio_edge_cases():
    x = np.full((64, 1), 0.25)
    assert sdr_db(x, x) is None, "完全一致时没有 dB 可报，不许报 +∞"
    assert energy_ratio_db(x, x) == pytest.approx(0.0)
    assert energy_ratio_db(x * 0.5, x) == pytest.approx(-6.0206, abs=1e-3)
    assert energy_ratio_db(x, np.zeros_like(x)) is None, "参考全零 ⇒ 比值无定义"
    with pytest.raises(ValueError):
        sdr_db(x, np.zeros((32, 1)), )
