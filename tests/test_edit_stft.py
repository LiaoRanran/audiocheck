"""P0 地基判据（二）：**时频编辑**（`core/edit/stft.py` + `BandGainOp`）。

为什么单独立一个文件：频域编辑与时间域增益的**效力边界不一样**，判据也必须分开写：

  1. **COLA / 重建**：`window_sq_sum` 恒 > 0（重叠归一化的分母）；恒等往返残差 = 浮点级；
  2. **参数守卫**：`hop > n_fft/2` 会静默丢样本 ⇒ 必须**拒绝**；
  3. **选区口径**：帧**中心**时刻 ∩ bin**中心**频率，双半开（与 `ops.BandGainOp` 逐位对齐）；
  4. **效力边界**：写出的跨度 = 选区 ± 一整个窗宽；跨度之外**逐位不变**，
     跨度之内未受影响的采样只有 ~1e-15 的往返残差 ⇒ **实测值**报在审计里；
  5. **不许有台阶**：选区边界上硬切窗宽会把被压掉的成分切出一个 click（实测过）——
     `test_band_boundary_has_no_step` 就是这条的回归判据；
  6. **测量互证**：导出后再用**测量内核**量一次，证明该压的压了、不该动的没动。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import (  # noqa: E402
    BandGainOp,
    GainOp,
    Project,
    render,
    render_band,
    stft,  # noqa: E402
)

FS = 48000
N_FFT = 4096
HOP = N_FFT // 4


def _sample_at(t: float, fs: int = FS) -> int:
    return int(math.ceil(t * fs - 1e-9))


def _write(path, x, subtype: str = "FLOAT", fs: int = FS, dtype=np.float32) -> str:
    sf.write(str(path), np.asarray(x, dtype=dtype), fs, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


def _gate(t: np.ndarray, a: float, b: float, ramp: float) -> np.ndarray:
    """`[a, b)` 的**升余弦**门（不是矩形门）。

    ⚠ 别改成矩形门：硬开硬关会在门边产生**全频段散射**（一个阶跃的谱是平的），
    于是"只压 7–9 kHz"的编辑会留下带外残留——那会污染下面的峰值/能量判据
    （实测：矩形门让导出峰值从 0.30 变成 0.33）。
    """
    g = ((t >= a) & (t < b)).astype(float)
    if ramp > 0:
        n = int(ramp * FS)
        w = 0.5 - 0.5 * np.cos(np.pi * np.arange(n) / n)
        i0, i1 = int(a * FS), int(b * FS)
        g[i0:i0 + n] = w
        g[i1 - n:i1] = w[::-1]
    return g


def _tone_mix(seconds: float = 2.0, fs: int = FS, parts=((440.0, 0.3),),
              gate: tuple[float, float] | None = None, ramp: float = 0.03) -> np.ndarray:
    """若干正弦的混合；`gate` 可让**最后一条**只在 `[gate[0], gate[1])` 出现（升余弦门）。"""
    n = int(seconds * fs)
    t = np.arange(n) / fs
    x = np.zeros(n)
    for i, (f, a) in enumerate(parts):
        if gate is not None and i == len(parts) - 1:
            x += a * _gate(t, gate[0], gate[1], ramp) * np.sin(2 * np.pi * f * t)
        else:
            x += a * np.sin(2 * np.pi * f * t)
    return np.column_stack([x, x])


def _amp_at(x: np.ndarray, f: float, k0: int, k1: int) -> float:
    """用**同一把尺子**量某个频率的幅度（Hann 窗 DFT，只对整周期频率准）。

    这是测出来的数，不是"看着像"：素材里的正弦都取整周期频率（440 / 8000 / 15000 在
    48 kHz 下 `f·N/fs` 为整数、且窗长取它们的公倍数），所以单点 DFT 就是解析幅度
    `A = 2·|X[k]| / Σw`。带外频率不会漏进这个 bin（Hann 只漏到 ±1 bin，而两个频率相隔很远）。
    """
    seg = np.asarray(x)[k0:k1, 0]
    w = np.hanning(len(seg))
    k = int(round(f * len(seg) / FS))
    return float(np.abs(np.fft.rfft(seg * w)[k]) * 2.0 / w.sum())


# ============================================================ 1. STFT 地基
def test_window_sq_sum_is_strictly_positive():
    """重叠归一化的分母**恒 > 0**（`docs/theory.md §12.1` 的 COLA/NOLA 落地）。

    `hop | n_fft` 时内区还必须是**常数**（Hann 的经典结论：`0.375·n_fft/hop`）；
    `hop ∤ n_fft` 时不要求常数（逐样本相除照样精确），但**不许有 0**。
    """
    for n_fft, hop in [(4096, 1024), (4096, 2048), (2048, 256), (1024, 300)]:
        frames = stft.frame_count(FS, n_fft, hop)
        wss = stft.window_sq_sum(n_fft, hop, frames)
        assert np.all(wss[n_fft:len(wss) - n_fft] > 0.0), (n_fft, hop, "分母出现 0 ⇒ 会丢样本")
    wss = stft.window_sq_sum(4096, 1024, stft.frame_count(FS, 4096, 1024))
    inner = wss[4096:len(wss) - 4096]
    assert np.allclose(inner, 0.375 * 4096 / 1024, rtol=1e-12), (
        "hop = n_fft/4 的内区窗平方和应当是常数 1.5，实测 %r–%r" % (inner.min(), inner.max()))


def test_roundtrip_residual_is_float_eps():
    """恒等往返的残差只有浮点舍入（~1e-15 相对）——这是"频谱编辑**不是**位精确"这条事实的**上界**。"""
    rng = np.random.default_rng(7)
    x = rng.standard_normal((FS, 2)) * 0.3
    for n_fft, hop in [(4096, 1024), (4096, 2048), (4096, 512), (2048, 256), (1024, 300)]:
        y = stft.roundtrip(x, n_fft=n_fft, hop=hop)
        assert y.shape == x.shape
        assert np.abs(y - x).max() < 1e-12, (n_fft, hop, np.abs(y - x).max())


def test_chunked_analysis_matches_whole_file():
    """分块（`pad=0` + 两侧真实上下文 + `keep` 只取中间）必须与整轨 `analyze` 得到**同一个**结果。

    频域渲染走的就是分块路径——它若与整轨不等价，"分块"就成了偷偷改了算法。
    """
    rng = np.random.default_rng(11)
    x = rng.standard_normal((FS + 137, 2)) * 0.3
    full = stft.roundtrip(x, n_fft=N_FFT, hop=HOP)
    cs, ce = 10000, 20000
    buf = x[cs - N_FFT:ce + N_FFT]
    S, meta = stft.analyze(buf, n_fft=N_FFT, hop=HOP, pad=0)
    got = stft.synthesize(S, meta, keep=slice(N_FFT, N_FFT + (ce - cs)))
    assert np.abs(got - full[cs:ce]).max() < 1e-12


def test_pad_zero_without_context_is_refused():
    """`pad=0` 只对"自带真实上下文、且只取中间"的调用方成立。

    直接喂**整段**信号时，帧网在末端被截断 ⇒ 重叠归一化的分母塌陷 ⇒ 浮点噪声被
    1/wss 放大成真实波形（实测放大到 1e-2 量级，比信号本身还响）。
    ⇒ 必须**抛错**，不许静默产出一段被放大的垃圾。
    """
    x = np.random.default_rng(3).standard_normal((FS, 1)) * 0.2
    S, meta = stft.analyze(x, n_fft=N_FFT, hop=HOP, pad=0)
    with pytest.raises(ValueError):
        stft.synthesize(S, meta)
    # 正确用法（补零一个窗宽）：整段可重建，长度与源一致
    S2, m2 = stft.analyze(x, n_fft=N_FFT, hop=HOP, pad=N_FFT)
    assert stft.synthesize(S2, m2).shape == x.shape


def test_hop_must_not_exceed_half_window():
    """`hop > n_fft/2` 时中间会有**没有任何窗覆盖**的样本 ⇒ 只能置 0（静默丢样本）。
    这种参数必须**当场拒绝**，而不是产出一段带洞的音频。"""
    with pytest.raises(ValueError):
        stft.check_params(1024, 700)
    with pytest.raises(ValueError):
        stft.analyze(np.zeros((FS, 1)), n_fft=1024, hop=700)
    stft.check_params(1024, 512)          # 恰好一半：合法


def test_bin_freqs_and_frame_times_are_centers():
    """两个轴的口径：bin = `k·fs/n_fft`（中心）；帧 = `起点 + n_fft/2`（中心）。

    `render.py` 的掩码直接用它们 ⇒ 这里钉住口径，别让"中心 vs 起点"悄悄漂。
    """
    bins = stft.bin_freqs(FS, N_FFT)
    assert bins.shape[0] == N_FFT // 2 + 1
    assert abs(bins[1] - FS / N_FFT) < 1e-9
    t = stft.frame_times(FS, N_FFT, HOP, 5, start_sample=-N_FFT)
    assert abs(t[0] - (-N_FFT + N_FFT / 2) / FS) < 1e-12, "第 0 帧的中心时刻不对"
    assert np.allclose(np.diff(t), HOP / FS), "帧中心间距应当是 hop/fs"


# ============================================================ 2. 频域渲染
def test_band_noop_is_bit_exact(tmp_path):
    """`gain_db == 0` 是 no-op：**整轨逐位不变**（连 STFT 都不该跑）。"""
    src = _write(tmp_path / "src.wav", _tone_mix())
    audit = render_band(src, str(tmp_path / "dst.wav"), 0.5, 1.0, 7000.0, 9000.0, 0.0)
    assert np.array_equal(_read(src), _read(tmp_path / "dst.wav")), "no-op 竟然改了采样"
    assert audit["ops"][0]["applied_frames"] == 0
    assert audit["roundtrip_floor_dbfs"] is None, "没跑 STFT 却报了往返残差"


def test_band_span_outside_is_bit_exact(tmp_path):
    """**效力边界**：写出的跨度 = 选区 ± 一整个窗宽 ⇒ 跨度之外**逐位不变**；
    跨度之内、受影响区（±半窗）之外的采样，只允许浮点级的往返残差。

    素材用 **float64（DOUBLE）**：float32 的量化底会散布在全频段、被掩码一起改，
    于是连"没被掩码覆盖"的采样也会动 1 个 float32 ULP（那由
    `test_band_unchanged_region_moves_at_most_one_float32_ulp` 单独钉住）。
    """
    src = _write(tmp_path / "src.wav", _tone_mix(gate=(0.5, 1.0)), subtype="DOUBLE",
                 dtype=np.float64)
    dst = str(tmp_path / "dst.wav")
    render_band(src, dst, 0.5, 1.0, 7000.0, 9000.0, -12.0)
    a, b = _read(src), _read(dst)
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    lo, hi = max(0, k0 - N_FFT), min(a.shape[0], k1 + N_FFT)
    assert np.array_equal(a[:lo], b[:lo]), "跨度**之前**的采样被动了"
    assert np.array_equal(a[hi:], b[hi:]), "跨度**之后**的采样被动了"
    # 跨度之内、受影响区之外：只有往返残差（实测 ~1e-16）
    quiet = np.concatenate([a[lo:k0 - N_FFT // 2], a[k1 + N_FFT // 2:hi]])
    got = np.concatenate([b[lo:k0 - N_FFT // 2], b[k1 + N_FFT // 2:hi]])
    assert np.abs(got - quiet).max() < 1e-12, "未受影响区不该有可闻改动"


def test_band_change_on_float32_source_is_at_most_one_peak_ulp(tmp_path):
    """float32 素材：**没被掩码覆盖**的采样也会动，但绝对量 ≤ **1 个峰值量级的 float32 ULP**。

    机制（实测，不是猜）：float32 的量化底散布在**全频段**上——素材里 3–9 kHz 的
    谱比谱峰低 **−145.5 dB**（正是 24 位 float32 的底）。band 掩码把这一段一起乘
    −40 dB ⇒ 带外频率的**波形**也随之变：实测 max|Δ| = 1.490e-08 = 2^-26，
    而素材峰值 0.3 的 float32 ULP 是 2^-25 —— 也就是**半个峰值 ULP**。

    ⚠ 判据锚在**素材峰值**上，不是每个采样各自的值：这个改动是"全频段底一起平移"，
    所以近零交叉的采样会有很多个"自己量级"的 ULP，但绝对量仍被峰值 ULP 管住。
    对 float32 源要求"逐位不变"**根本做不到**，写上去就是自欺。带内衰减的刀口另由
    `test_band_gain_hits_the_band_only`（−40 dB）钉住。
    """
    src = _write(tmp_path / "src.wav", _tone_mix(parts=((440.0, 0.3),)))     # FLOAT = float32
    dst = str(tmp_path / "dst.wav")
    render_band(src, dst, 0.5, 1.0, 3000.0, 9000.0, -40.0)      # 440 在带外
    a, b = _read(src), _read(dst)
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    inner = slice(k0 + N_FFT, k1 - N_FFT)
    diff = np.abs(b[inner] - a[inner])
    peak_ulp = float(np.spacing(np.float32(np.abs(a).max())))     # 峰值量级上的 1 个 ULP
    assert diff.max() <= peak_ulp, (
        "带外波形的改动 %.3e 超过了 1 个峰值 ULP（%.3e）" % (diff.max(), peak_ulp))
    assert diff.max() > 1e-9, (
        "量化底没被碰？(%.3e) 那这条判据失去意义了——素材可能不是 float32" % diff.max())


def test_band_gain_hits_the_band_only(tmp_path):
    """只压带内：8 kHz 那条线掉 −40 dB；**带外**的 440 与 15 kHz 一分不动。"""
    src = _write(tmp_path / "src.wav",
                 _tone_mix(parts=((440.0, 0.3), (15000.0, 0.15), (8000.0, 0.4))))
    dst = str(tmp_path / "dst.wav")
    render_band(src, dst, 0.3, 1.7, 7000.0, 9000.0, -40.0)
    a, b = _read(src), _read(dst)
    k0, k1 = _sample_at(0.6), _sample_at(1.4)          # 避开边界过渡带
    drop = 20 * math.log10(_amp_at(b, 8000.0, k0, k1) / _amp_at(a, 8000.0, k0, k1))
    assert abs(drop + 40.0) < 1.0, "带内衰减不是 −40 dB，实测 %.2f" % drop
    for f in (440.0, 15000.0):
        keep = 20 * math.log10(_amp_at(b, f, k0, k1) / _amp_at(a, f, k0, k1))
        assert abs(keep) < 0.05, "带外 %.0f Hz 被动了 %.4f dB" % (f, keep)


def test_band_boundary_has_no_step(tmp_path):
    """⚠ **回归判据**（这条是实测踩出来的，不是保险起见）。

    素材 = 整轨纯 8 kHz 正弦，把**整段** −60 dB ⇒ 导出后只剩"窗锥形恢复"的残包络。
    若写出的范围与选区**齐平**（不外扩），掩码帧的重建会在选区左边界被硬切 ⇒
    在 b 里留下一个**单采样台阶**（实测：8 kHz 幅度从 0.2 掉到 0.61×0.2 = 0.122，
    包络跳变 0.078）。外扩一个窗宽后，恢复由窗自身完成 ⇒ 逐采样跳变只有 ~1e-4 量级。
    用 Hilbert 包络的**最大逐采样跳变**把两者分开（阈值 0.01）。
    """
    src = _write(tmp_path / "src.wav", _tone_mix(parts=((8000.0, 0.2),)))
    dst = str(tmp_path / "dst.wav")
    render_band(src, dst, 0.6, 1.4, 7000.0, 9000.0, -60.0)
    b = _read(dst)[:, 0]
    env = np.abs(signal.hilbert(b))
    step = np.abs(np.diff(env)).max()
    assert step < 0.01, (
        "边界上出现了台阶（逐采样包络跳变 %.4f）——写出的跨度必须外扩 n_fft" % step)


def test_band_audit_reports_measured_roundtrip_floor(tmp_path):
    """审计字段：**实测**的往返残差、采样数口径、被改的时频点数、STFT 参数。

    素材取**单频 0.5 的 1 kHz**（`sin(2πk/48)` 在 k=12 处恰好取到 ±1）⇒ 输出峰值
    必须是确定的 −6.02 dBFS，这条同时钉住"峰值是**输出**上量的、不是源上量的"。
    """
    src = _write(tmp_path / "src.wav", _tone_mix(parts=((1000.0, 0.5),)))
    dst = str(tmp_path / "dst.wav")
    audit = render_band(src, dst, 0.5, 1.0, 7000.0, 9000.0, -40.0)
    assert audit["domain"] == "band"
    assert audit["n_fft"] == N_FFT and audit["hop"] == HOP
    assert audit["window"] == "periodic_hann"
    floor = audit["roundtrip_floor_dbfs"]
    assert floor is not None and floor < -200.0, (
        "往返残差应当只有浮点级（< −200 dBFS），实测 %r" % floor)
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    span = min(audit["frames"], k1 + N_FFT) - max(0, k0 - N_FFT)
    assert audit["roundtrip_floor_samples"] == span * audit["channels"], (
        "残差是在多少个采样上实测的，口径必须与写出的跨度一致")
    op = audit["ops"][0]
    assert op["kind"] == "band_gain" and op["gain_db"] == -40.0
    assert op["applied_frames"] == _sample_at(1.0) - _sample_at(0.5)
    assert op["applied_tf_points"] > 0
    assert abs(audit["peak_out_dbfs"] + 6.02) < 0.01, (
        "输出峰值应为 0.5 的 −6.02 dBFS，实测 %r" % audit["peak_out_dbfs"])


def test_chunk_size_does_not_change_the_result(tmp_path):
    """**分块粒度不许改变结果**：把 `chunk_hops` 从默认 512 缩到 4（每块只有 4 个 hop），
    导出必须与单块**逐位相同**。

    这条盯的是接缝：块的帧网格、两侧上下文、掩码取到的帧集合都会随块边界变——
    任一处没对齐，接缝上就会出现两套相位（听感上是周期性的"哒"）。
    """
    src = _write(tmp_path / "src.wav",
                 _tone_mix(parts=((440.0, 0.3), (8000.0, 0.4)), gate=(0.5, 1.0)))
    one, many = str(tmp_path / "one.wav"), str(tmp_path / "many.wav")
    render_band(src, one, 0.3, 1.2, 7000.0, 9000.0, -20.0)                  # 单块
    a = render_band(src, many, 0.3, 1.2, 7000.0, 9000.0, -20.0, chunk_hops=4)  # 13 块
    assert np.array_equal(_read(one), _read(many)), "分块粒度改变了导出结果"
    assert a["roundtrip_floor_dbfs"] == max(a["roundtrip_floor_dbfs"], -999.0)   # 有实测值
    assert a["roundtrip_floor_samples"] > 0


def test_band_op_rejects_bad_selection():
    with pytest.raises(ValueError):
        BandGainOp(1.0, 0.5, 100.0, 200.0, -3.0)
    with pytest.raises(ValueError):
        BandGainOp(0.0, 1.0, 200.0, 200.0, -3.0)          # f1 必须 > f0
    assert BandGainOp(0.0, 1.0, 100.0, 200.0, 0.0).is_noop


def test_mixing_time_and_band_ops_is_refused(tmp_path):
    """时间域与频域操作**不许混用**（顺序语义没立判据）⇒ 必须当场拒绝，不许猜。"""
    src = _write(tmp_path / "src.wav", _tone_mix())
    p = Project(src, [GainOp(0.0, 1.0, -3.0), BandGainOp(0.0, 1.0, 100.0, 200.0, -3.0)])
    with pytest.raises(ValueError):
        render(p, str(tmp_path / "dst.wav"))


# ============================================================ 3. 与测量内核互证
def test_band_measurement_crosscheck(tmp_path):
    """**用测量内核再量一次**：压掉 8 kHz 那条线后，峰值必须落在剩下的 440 上。

    素材：0.3 的 440（整轨）+ 0.4 的 8 kHz（只在 `[0.5, 1.0)`）——8 kHz **只出现在选区
    内部**，于是"选区内被压掉"这件事直接变成峰值的变化：导出后峰值应当是
    20·log10(0.3) = −10.46 dBFS。
      * 若那条线没被压掉 ⇒ 峰值还在 −5 dBFS 一带 ⇒ 红；
      * 若连 440 一起压了 ⇒ 峰值掉到 −70 dBFS 量级 ⇒ 红；
      * 若"只改了半截"（时间选区的口径错位）⇒ 8 kHz 还在 ⇒ 红。
    """
    from audiocheck.core.engine import analyze

    src = _write(tmp_path / "src.wav",
                 _tone_mix(parts=((440.0, 0.3), (8000.0, 0.4)), gate=(0.5, 1.0)))
    dst = str(tmp_path / "dst.wav")
    render_band(src, dst, 0.3, 1.2, 7000.0, 9000.0, -60.0)

    p_src = analyze(src, deep=False)["peak_dbfs"]
    p_dst = analyze(dst, deep=False)["peak_dbfs"]
    assert p_src > -6.0, "素材峰值偏低（%.2f），这条判据就没区分力了" % p_src
    assert abs(p_dst - 20 * math.log10(0.3)) < 0.15, (
        "导出后峰值 %.2f dBFS ≠ 剩下的 440（%.2f）" % (p_dst, 20 * math.log10(0.3)))
