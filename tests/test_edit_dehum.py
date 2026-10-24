"""去嗡（`DehumOp` / `dehum.dehum`）的判据 —— 全部对着**真值**量。

真值怎么来：`y = clean + 嗡`，嗡的频率/幅度我们说了算（`dehum_probe.hum` 同款构造）。
于是"去掉了多少"与"改坏了多少"都能用 `metrics.sdr_db(out, clean)` 与逐谐波
`band_gain_db` 量出来——这正是 `docs/editor_roadmap.md §5` 要求的形态。

本文件钉住的三件事（对应 `dehum.py` 里的实测表）：
1. **压制量**：50/100/150 Hz 各压 ≥15 dB，且 SDR 相对真值提升 ≥10 dB；
2. **Q=10 的理由**：嗡漂到 51 Hz（滤波仍按 50 Hz 设计）时仍要压 ≥15 dB；
3. **零相位**：脉冲峰不移动（因果滤波会把峰推后）——这是选 `filtfilt` 的判据。
"""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from audiocheck.core.edit import DehumOp, GainOp, Project, render
from audiocheck.core.edit import dehum as dh
from audiocheck.core.edit.corpus import band_limited_noise
from audiocheck.core.edit.metrics import band_gain_db, sdr_db

FS = 48000


def _hum(clean: np.ndarray, f0: float, f_rel_db: float, harmonics=(1, 2, 3),
         fs: int = FS, seed: int = 20261022) -> np.ndarray:
    """叠嗡：幅度相对 `clean` 峰值（`f_rel_db`），相位固定 seed。"""
    n = clean.shape[0]
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    h = np.zeros(n)
    for k in harmonics:
        h += np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 2 * np.pi)) / k
    peak = float(np.abs(clean).max())
    h *= (10 ** (f_rel_db / 20.0)) * peak / float(np.abs(h).max())
    return clean + h[:, None]


# --------------------------------------------------------------- 算子本体
def test_suppresses_the_first_three_harmonics_and_improves_sdr():
    """带限白噪 + 嗡（−20 dB）：三个谐波各压 ≥15 dB，SDR 相对真值提升 ≥10 dB。"""
    clean = band_limited_noise(3.0, FS, 18000.0, amp=0.5)
    noisy = _hum(clean, 50.0, -20.0)
    out, st = dh.dehum(noisy, 50.0, FS)
    assert st["harmonics_used"] == [1, 2, 3]
    for k, g in zip((1, 2, 3), st["suppression_db"]):
        assert g is not None and g <= -15.0, "第 %d 次谐波只压了 %s dB" % (k, g)
    assert sdr_db(out, clean) - sdr_db(noisy, clean) >= 10.0
    # 能量不许被顺手压小（PHAIN 的教训：变安静不是修好）
    assert abs(float(10 * np.log10(np.sum(out ** 2) / np.sum(noisy ** 2)))) < 0.5


def test_q10_still_suppresses_a_one_hertz_drifted_hum():
    """★ Q=10 的**唯一理由**：电网/转档不是理想 50.000 Hz。

    嗡实际在 **51 Hz**，而滤波器按 **50 Hz** 设计（真实用法：用户按标称值给 f0）。
    Q=10 的陷波宽度 `f0/Q = 5 Hz` ⇒ 仍压 ≥15 dB；Q=40（宽 1.25 Hz）只剩约 3 dB。
    """
    clean = band_limited_noise(3.0, FS, 18000.0, amp=0.5)
    noisy = _hum(clean, 51.0, -20.0)
    out10, _ = dh.dehum(noisy, 50.0, FS, q=10.0)
    g10 = band_gain_db(noisy, out10, FS, 45.0, 55.0)
    assert g10 is not None and g10 <= -15.0, "Q=10 在 1 Hz 漂移下只压了 %s dB" % g10
    out40, _ = dh.dehum(noisy, 50.0, FS, q=40.0)
    g40 = band_gain_db(noisy, out40, FS, 45.0, 55.0)
    assert g40 is not None and g40 > -10.0, "Q=40 在 1 Hz 漂移下竟然压了 %s dB" % g40


def test_zero_phase_impulse_response_is_symmetric():
    """零相位判据：脉冲响应**左右对称**（= 相位为零，能量不往一个方向拖）。

    这不是"更高级"，是**离线修复**的合理选择：没有实时性约束，就没有理由付相位失真
    （实测同 Q 下 SDR 高 2–3.6 dB，见 `dehum.py` 的表）。
    ⚠ 不能拿"峰的位置"当判据：陷波器的直接项 `b0≈1` 最大，因果滤波的峰**也**在原位
    （实测踩过）——对称性才是零相位的定义性质。
    """
    n = 32768
    x = np.zeros((n, 1))
    x[n // 2, 0] = 1.0
    out, _st = dh.dehum(x, 50.0, FS)
    seg = out[:, 0]
    m = n // 2
    # 对称性只在**脉冲响应充分衰减**后成立：Q=10 @50 Hz 的衰减时间 ~64 ms（3072 采样），
    # 所以信号要够长、比较窗要远离两端。实测最大不对称 2.7e-9（峰 0.998），
    # 因果对照是 3.9e-3（差 6 个数量级）⇒ 判据用 1e-7。
    assert np.allclose(seg[m + 1:m + 500], seg[m - 1:m - 500:-1], rtol=0, atol=1e-7)
    # 因果对照：同一组陷波做单向 lfilter ⇒ 脉冲响应**不对称**
    from scipy import signal
    y = x.copy()
    for k in (1, 2, 3):
        b, a = signal.iirnotch(k * 50.0, 10.0, FS)
        y = signal.lfilter(b, a, y, axis=0)
    assert np.max(np.abs(y[m + 1:m + 300, 0] - y[m - 1:m - 300:-1, 0])) > 1e-4


def test_defaults_are_the_measured_ones():
    """默认值不是手感：Q=10、谐波 1/2/3（改它必须是一次有实测支撑的决定）。"""
    assert dh.DEFAULT_Q == 10.0
    assert dh.DEFAULT_HARMONICS == (1, 2, 3)
    assert dh.MEASURE_HALF_HZ == 5.0


def test_harmonics_above_nyquist_are_skipped_and_reported():
    """`k·f0 ≥ fs/2` 的谐波**跳过并记账**（不猜着折回、不静默丢）。"""
    x = band_limited_noise(1.0, 8000, 3000.0, amp=0.5)
    out, st = dh.dehum(x, 3000.0, 8000, harmonics=(1, 2))
    assert st["harmonics_used"] == [1]
    assert st["skipped_harmonics"] == [2]
    assert out.shape == x.shape


def test_short_selection_is_refused():
    """`filtfilt` 的 `padlen=9`：少于 10 个采样**明确拒绝**，**恰好 10 个要放行**。

    `MIN_SAMPLES = 10` 的等号侧：9 拒 / 10 收（`<` 改成 `<=` 会把 10 也拒掉）。
    """
    with pytest.raises(ValueError):
        dh.dehum(np.zeros((9, 1)), 50.0, FS)
    x10 = np.full((10, 1), 0.1)
    out, st = dh.dehum(x10, 50.0, FS)                   # 10 = padlen+1，可定义
    assert out.shape == (10, 1) and st["changed_samples"] == 10
    with pytest.raises(ValueError, match="基频"):
        dh.dehum(np.zeros((10, 1)), 0.0, FS)             # 基频非法（必须是这条信息）
    with pytest.raises(ValueError):
        dh.dehum(np.zeros((10, 1)), 50.0, FS, q=0.0)


def test_all_harmonics_out_of_range_is_refused():
    """全部谐波越界 ⇒ 拒绝，且**信息里印的是真的奈奎斯特**（fs/2，不是 fs·2）。

    "没有可滤的成分"不许当成成功；错误信息也是口径（印错数字等于让人查错方向）。
    """
    with pytest.raises(ValueError, match="50.0 Hz"):
        dh.dehum(np.zeros((64, 1)), 60.0, 100, harmonics=(1, 2))


def test_suppression_is_rounded_to_three_decimals():
    """`suppression_db` 保留 **3** 位（原始值是 −27.1060443…）。4 位会写成 −27.1060。

    展示位数也是口径：给人看的 dB 值要和审计/CLI 打印的位数一致。
    """
    clean = band_limited_noise(3.0, FS, 18000.0, amp=0.5)
    _out, st = dh.dehum(_hum(clean, 50.0, -20.0), 50.0, FS)
    assert st["suppression_db"] == [-27.106, -23.806, -27.071]


# --------------------------------------------------------------- 操作模型
def test_dehum_op_validates_and_describes():
    op = DehumOp(0.0, 3.0, 50.0)
    assert op.q == dh.DEFAULT_Q and op.harmonics == dh.DEFAULT_HARMONICS
    assert op.is_noop is False
    assert op.to_dict()["kind"] == "dehum"
    assert op.to_dict()["harmonics"] == [1, 2, 3]
    assert "50.00 Hz" in op.describe() and "given" in op.describe() and "Q=10" in op.describe()
    for bad in (dict(f0=0.0), dict(q=0.0), dict(harmonics=()),
                dict(harmonics=(0,)), dict(harmonics=(1.5,))):
        with pytest.raises(ValueError):
            DehumOp(0.0, 3.0, **{"f0": 50.0, **bad})


# --------------------------------------------------------------- 渲染路径
def _write(tmp_path, x, fs=FS, name="src.wav"):
    p = tmp_path / name
    sf.write(str(p), x, fs, subtype="FLOAT")
    return str(p)


def test_render_changes_the_selection_and_nothing_else(tmp_path):
    """**选区之外逐位不变**；选区之内**整段都被改动**（LTI 的如实口径）。

    审计必须写清：`changed_samples == 选区采样数`，且每个谐波有**实测**压制量。
    """
    clean = band_limited_noise(2.0, FS, 18000.0, amp=0.4)
    noisy = _hum(clean, 50.0, -20.0)
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    t0, t1 = 0.5, 1.5
    audit = render(Project(src, [DehumOp(t0, t1, 50.0)]), dst)
    assert audit["domain"] == "dehum"
    assert audit["algorithm"] == "zerophase_notch_cascade"
    op = audit["ops"][0]
    k0, k1 = 24000, 72000
    assert op["applied_frames"] == k1 - k0
    assert op["changed_samples"] == k1 - k0          # 整段选区都被改动（LTI）
    for g in op["suppression_db"]:
        assert g is not None and g <= -15.0
    assert op["energy_ratio_measured_db"] is not None
    y, _ = sf.read(dst, dtype="float64", always_2d=True)
    x, _ = sf.read(src, dtype="float64", always_2d=True)
    assert np.array_equal(y[:k0], x[:k0]), "选区之前被改了"
    assert np.array_equal(y[k1:], x[k1:]), "选区之后被改了"
    assert not np.array_equal(y[k0:k1], x[k0:k1])


def test_render_refuses_overlapping_dehum_selections(tmp_path):
    src = _write(tmp_path, np.zeros((FS, 1)))
    dst = str(tmp_path / "out.wav")
    with pytest.raises(ValueError, match="重叠"):
        render(Project(src, [DehumOp(0.0, 0.6, 50.0), DehumOp(0.4, 0.8, 60.0)]), dst)


def test_render_chains_dehum_with_other_domains(tmp_path):
    """混用**不再拒绝**：2026-10-24 起按规范顺序链式渲染，审计逐阶段可查。"""
    src = _write(tmp_path, np.zeros((FS, 1)))
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, [GainOp(0.0, 0.5, -3.0), DehumOp(0.0, 0.5, 50.0)]), dst)
    assert audit["chained"] is True
    assert [s["op"]["kind"] for s in audit["stages"]] == ["dehum", "gain"]
