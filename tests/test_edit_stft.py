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


def test_band_audit_reports_measured_attenuation(tmp_path):
    """审计里的**实测衰减**：设定 −40 dB ⇒ 实测 −40.00 dB（±0.01）。

    ⚠ 口径是"**同一套 STFT 表示**里量"（掩码前/后的带内能量比），不是"在导出信号上再量一次"：
    后者会被分析窗的泄漏压浅（宽带素材实测 −18.6 vs 设定 −20，见
    `tests/test_edit_metrics.py::test_band_gain_db_is_shallower_than_set_on_broadband_material`）。
    "我压了 40 dB"这句话必须有**实测值**撑着——这就是"可审计的修复"。
    """
    src = _write(tmp_path / "src.wav", _tone_mix(parts=((8000.0, 0.3),)))     # 带内单频
    audit = render_band(src, str(tmp_path / "dst.wav"), 0.5, 1.0, 7000.0, 9000.0, -40.0)
    m = audit["ops"][0]["attenuation_measured_db"]
    assert m is not None and abs(m + 40.0) < 0.01, m

    # no-op：没跑掩码 ⇒ 报 `None`（不许报 0.0 冒充"量过了"）
    noop = render_band(src, str(tmp_path / "n.wav"), 0.5, 1.0, 7000.0, 9000.0, 0.0)
    assert noop["ops"][0]["attenuation_measured_db"] is None


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


# ============================================================ 4. 口径与默认值
# 下面这批判据是从 2026-10-18 变异探针的**存活清单**转化来的：每一条都对应一个
# "改一个字符也测不出来"的位置（`<`↔`<=`、默认值、守卫子句）。§ 见 `packaging/mutation_probe.py`。


def test_smallest_legal_window_is_accepted():
    """`n_fft` 的下限判据是 `< 2`：`n_fft = 2` **合法**，`1` 才该拒。"""
    stft.check_params(2, 1)
    with pytest.raises(ValueError):
        stft.check_params(1, 1)


def test_analyze_accepts_exactly_one_frame():
    """帧数下限判据是 `frames < 1`：**恰好 1 帧**要能跑（`<= 1` 会把它也拒掉）。

    构造：`pad=0` 且信号长度 = `n_fft` ⇒ `ceil((n−n+1)/hop) = 1` 帧。
    """
    S, meta = stft.analyze(np.zeros((1024, 1)), n_fft=1024, hop=256, pad=0)
    assert meta["frames"] == 1 and S.shape == (513, 1, 1)
    with pytest.raises(ValueError):                    # 空信号 ⇒ 0 帧 ⇒ 拒
        stft.analyze(np.zeros((0, 1)), n_fft=1024, hop=256, pad=0)


def test_default_stft_params_are_pinned():
    """默认参数是**接口契约**：`n_fft=4096`、`hop=n_fft//4`。

    用**行为**钉住（不查签名）：不带参数与显式传 (4096, 1024) 必须**逐位相同**；
    改了默认值这条就红（默认值一改，`bin_freqs`/`frame_times` 的全部口径跟着漂）。
    """
    x = np.random.default_rng(23).standard_normal((2048, 1)) * 0.3
    S1, m1 = stft.analyze(x)
    S2, m2 = stft.analyze(x, n_fft=4096, hop=1024)
    assert (m1["n_fft"], m1["hop"]) == (4096, 1024)
    assert np.array_equal(S1, S2)
    assert np.array_equal(stft.roundtrip(x), stft.roundtrip(x, n_fft=4096, hop=1024))


def test_synthesize_refuses_a_region_with_collapsed_overlap():
    """覆盖守卫的**第二个子句**：区间合法、但里面**有样本的分母塌陷** ⇒ 也要抛错。

    构造：`pad=0`、只取 OLA 的**最后一个样本**——那里只有一个窗、`w[n−1]` 是周期性 Hann
    在 `k=n−1` 处的值（`n=4096` 时 `w[n−1] ≈ 5.9e-7` ⇒ `Σw² ≈ 3.5e-13 ≤ _WSS_MIN(1e-12)`）。
    ⚠ 窗长要**够大**才塌到阈值以下：`n=1024` 时 `w²(n−1) ≈ 8.9e-11`，仍高于阈值。
    这一条同时管住守卫里的 `or` 与 `_WSS_MIN` 的取值：少了 `or` 的子句、
    或把下限设成 0，都会让这里**静默产出放大后的浮点噪声**。
    """
    S, meta = stft.analyze(np.zeros((4096, 1)), n_fft=4096, hop=1024, pad=0)
    lp = (S.shape[1] - 1) * 1024 + 4096
    with pytest.raises(ValueError):
        stft.synthesize(S, meta, keep=slice(lp - 1, lp))
    # 反向：正常区间**不许**被守卫误拦（样本 ≥ (R−1)·hop 时 R 个窗齐备、分母 = 1.5）
    S2, m2 = stft.analyze(np.zeros((12288, 1)), n_fft=4096, hop=1024, pad=0)
    assert stft.synthesize(S2, m2, keep=slice(3072, 3082)).shape == (10, 1)


def test_open_ended_keep_means_to_the_default_end():
    """`keep=slice(a, None)` = "从 a 取到**默认末尾**"（末尾是 `pad+length`，不是 `pad−length`）。"""
    x = np.random.default_rng(5).standard_normal((4096, 1))
    S, meta = stft.analyze(x, n_fft=1024, hop=256)
    full = stft.synthesize(S, meta)
    got = stft.synthesize(S, meta, keep=slice(1024, None))
    assert got.shape == full.shape and np.array_equal(got, full)


# ========== 5. 掩码口径的"数得出来"判据（2026-10-18 变异探针存活清单转来）==========


def test_mask_tf_point_count_pins_both_half_open_edges(tmp_path):
    """掩码覆盖的时频点数**必须精确**：两条半开区间的边都靠它钉住。

    推导（`fs=48000, n_fft=4096, hop=1024`，选区 `[t0,t1) s`，带 `[7000,9000) Hz`）：
      * bin 中心 = `k·11.71875`；`[7000,9000)` ⇒ `k = 598..767` ⇒ **170** 个
        （`k = 768` 恰好 9000.0 Hz ⇒ **不算**，半开上界）；
      * 帧中心 = `(起点 + n_fft/2)/fs`；本跨度下首个中心 = `21952/48000 = 0.457333 s`、
        每帧 +`1024/48000`；`t0 = 0.5 s` 时 `m = 2` 的中心**恰好** 0.5 ⇒ **算**（半开下界）。

    ⇒ `t1 = 1.0 s`（无帧中心落在其上）时 = `170 × 24 = 4080`；
    ⇒ `t1 = 47552/48000 s`（**故意让 `m = 25` 的中心恰好落在 `t1` 上**）时 = `170 × 23 = 3910`
    ——上界**半开**，那一个帧**不算**。
    ⚠ 这两个数是**快照**：改默认 `n_fft/hop` 或掩码口径就要重新推导（这正是它们的作用）。
    """
    src = _write(tmp_path / "src.wav", _tone_mix(parts=((8000.0, 0.3),)))
    a1 = render_band(src, str(tmp_path / "d1.wav"), 0.5, 1.0, 7000.0, 9000.0, -40.0)
    n1 = a1["ops"][0]["applied_tf_points"]
    assert n1 == 170 * 24 == 4080, n1
    assert type(n1) is int, "时频点数是**计数**，不许变成浮点（4080.0 也是错的）"

    t1 = 47552.0 / FS          # = 0.990666…，刻意让帧中心恰好落在 t1 上
    a2 = render_band(src, str(tmp_path / "d2.wav"), 0.5, t1, 7000.0, 9000.0, -40.0)
    n2 = a2["ops"][0]["applied_tf_points"]
    assert n2 == 170 * 23 == 3910, ("上界必须半开：落在 t1 上的那一帧不该算，实得 %d" % n2)


def test_frequency_lower_edge_is_inclusive_on_a_bin_centre(tmp_path):
    """频率下界 `>= f0` 是**闭**的：`f0` 恰好是 bin 中心时，那条 bin **要**被压。

    构造：`n_fft=4096`、fs=48000 ⇒ 格点 `k·11.71875`；取 `f0 = 7031.25 = 600·11.71875`
    与一条**恰好 7031.25 Hz** 的正弦。

    ⚠ 这里的实测衰减**不是 −40 dB，而是 −15 dB**——这是**正确的**：那条正弦在 Hann 窗下
    的能量摊在 bin **599/600/601** 上，而 599（7019.5 Hz）**在带外**（< f0）⇒ 它没被压。
    判据因此写成**区间**并把机制写清楚：把 `>=` 改成 `>` 会连 bin 600 也放过，
    保留的能量明显更多（衰减更浅）⇒ 立刻越界。
    """
    f = 7031.25
    t = np.arange(2 * FS) / FS
    src = _write(tmp_path / "src.wav", (0.3 * np.sin(2 * np.pi * f * t))[:, None])
    dst = str(tmp_path / "dst.wav")
    render_band(src, dst, 0.5, 1.0, f, 9000.0, -40.0, overwrite=True)
    a, b = _read(src), _read(dst)
    k0 = int(0.65 * FS)                       # 窗口 16384 ⇒ 7031.25 正好落在整格点上
    drop = 20 * math.log10(_amp_at(b, f, k0, k0 + 16384) / _amp_at(a, f, k0, k0 + 16384))
    assert -16.0 < drop < -14.0, (
        "下界（%g Hz 恰为 bin 中心）应压掉 bin 600（实测 −15.1 dB，邻居 599 在带外）；"
        "写成严格 `>` 会浅得多，实得 %.2f" % (f, drop))


def test_silence_material_reports_no_peak_and_no_floor(tmp_path):
    """**数字静音**素材：`peak` 与往返残差都是 0 ⇒ 两个字段都必须报 `None`。

    ⚠ 判据写的是 `if peak > 0` / `if floor > 0`：改成 `>= 0` 会去算 `log10(0) = −inf`
    再 `round(−inf)` ⇒ **直接抛异常**（不是报个错数）。所以这条同时钉住"静音不崩"。
    """
    t = np.arange(2 * FS) / FS
    src = _write(tmp_path / "src.wav", np.zeros((len(t), 1)))
    audit = render_band(src, str(tmp_path / "dst.wav"), 0.5, 1.0, 7000.0, 9000.0, -40.0)
    assert audit["peak_out_dbfs"] is None
    assert audit["roundtrip_floor_dbfs"] is None
    assert audit["ops"][0]["attenuation_measured_db"] is None      # 带内本来没能量


def test_audit_numbers_keep_their_declared_precision(tmp_path):
    """审计里的每个数都**按声明的位数**落定：小数位多一位、量级换算写错，这里都会红。

    * `duration_s` 是 `round(frames/fs, 6)`：用**帧数不是整秒**的素材（48001 帧）才分得出 6 位与 7 位；
    * `peak_out_dbfs` / `roundtrip_floor_dbfs` 是 `round(…, 3)`：所以它们必须**自己等于**
      `round(自己, 3)`（4 位小数就说明写成了 `round(…, 4)`）；
    * 残差的**量级**由 float64 的舍入决定（≈2e-16 ⇒ −300 dB 量级）：写成 `30·log10` 会跑到 −450 dB
      那种"好得不像真的"的数上——所以判据钉成 **区间** 而不是"只要很小就行"。
    """
    t = np.arange(48001) / FS                                   # 48001 帧 = 1.0000208… s
    src = _write(tmp_path / "src.wav", (0.5 * np.sin(2 * np.pi * 1000 * t))[:, None],
                 subtype="DOUBLE", dtype=np.float64)
    audit = render_band(src, str(tmp_path / "dst.wav"), 0.5, 0.9, 7000.0, 9000.0, -40.12345)
    assert audit["duration_s"] == round(48001 / FS, 6), audit["duration_s"]
    assert audit["peak_out_dbfs"] == round(20 * math.log10(0.5), 3), audit["peak_out_dbfs"]
    floor = audit["roundtrip_floor_dbfs"]
    assert round(floor, 3) == floor, "残差报了 4 位小数：%r" % floor
    assert -320.0 < floor < -300.0, (
        "float64 的往返残差应当在 −300 dB 量级（实测 −307），实得 %r" % floor)
    m = audit["ops"][0]["attenuation_measured_db"]
    # 实测值是 `round(…, 3)` ⇒ 只保留 3 位小数（这正是钉住"位数"的那一条）
    assert round(m, 3) == m and abs(m + 40.12345) < 1e-3, m


def test_context_offset_is_right(tmp_path):
    """"自带上下文的偏移量"错了会**整体挪动一段**（而稳态素材上听不出来）。

    构造：一条只出现在 `[0.6,0.7) s` 的 8 kHz 正弦 + 选区 `[0.5,1.0)`。
    `buf[lo − src_lo : …] = raw` 的偏移写错 ⇒ 那段音频被挪到别处 ⇒ `[0.6,0.7)` 这个窗口
    里就**没有**该来的内容了（实测衰减会变成"没有可比对象"）。
    """
    from audiocheck.core.edit.metrics import band_gain_db

    src = _write(tmp_path / "src.wav", _tone_mix(parts=((8000.0, 0.3),), gate=(0.6, 0.7)))
    dst = str(tmp_path / "dst.wav")
    render_band(src, dst, 0.5, 1.0, 7000.0, 9000.0, -40.0, overwrite=True)
    a, b = _read(src), _read(dst)
    k0, k1 = int(0.61 * FS), int(0.69 * FS)
    got = band_gain_db(a, b, FS, 7000.0, 9000.0, k0, k1)
    assert got is not None and abs(got + 40.0) < 1.0, (
        "带内那段（0.61–0.69 s）应该被压 40 dB，实得 %r ⇒ 上下文偏移错了" % got)


def test_truncated_wav_is_self_corrected_by_libsndfile(tmp_path):
    """⚠ **WAV 被截断时 libsndfile 会按文件大小重算帧数**（`f.frames` 就等于实有帧数）。

    实测（本机 libsndfile）：3 s 立体声 PCM_16 WAV 砍到 45% 字节后，
    `sf.info()` 报的是 **64793 帧**（= 实有），不是 WAV 头里写的 144000 ⇒
    我们**看不到**"声称 vs 实读"的差 ⇒ `read_incomplete = False` 是**对的**（不误报）。
    ⚠ 换个容器就不一样：截断的 **FLAC** 仍声称 144000 帧，读到损坏处会**抛**
    `LibsndfileError`（实测）⇒ 走 `except` 分支、`read_incomplete = True`
    （见 `test_edit_render.py` 的截断判据）。
    ⇒ 也就是说 `render._render_band` 里那条"**短读**"分支（`len(raw) < hi − lo`）
    在**已验证的两种容器上不可达**；它留着是**防御性**的（未穷举其它容器）。
    """
    src = str(tmp_path / "src.wav")
    sf.write(src, _tone_mix(parts=((440.0, 0.3),)).astype(np.float32), FS, subtype="PCM_16")
    raw = open(src, "rb").read()
    cut = int(len(raw) * 0.45)
    open(src, "wb").write(raw[:cut])
    avail = max(0, (cut - 44) // (2 * 2))            # 44 = 标准 PCM WAV 头；2 声道 × 2B
    assert sf.info(src).frames == avail, "libsndfile 没有按文件大小重算帧数（假设已变）"
    audit = render_band(src, str(tmp_path / "dst.wav"), 1.0, 1.4, 7000.0, 9000.0, -30.0,
                        overwrite=True)
    assert audit["read_incomplete"] is False, "WAV 截断被误报成'读不满'了"
    assert audit["frames"] == avail, audit["frames"]


def test_band_op_rejects_bad_selection():
    with pytest.raises(ValueError):
        BandGainOp(1.0, 0.5, 100.0, 200.0, -3.0)
    with pytest.raises(ValueError):
        BandGainOp(0.0, 1.0, 200.0, 200.0, -3.0)          # f1 必须 > f0
    assert BandGainOp(0.0, 1.0, 100.0, 200.0, 0.0).is_noop


def test_mixing_time_and_band_ops_is_chained(tmp_path):
    """时间域与频域混用**不再拒绝**（2026-10-24）：按规范顺序链式渲染
    （频域在前、时间域在后），审计逐阶段可查。"""
    src = _write(tmp_path / "src.wav", _tone_mix())
    p = Project(src, [GainOp(0.0, 1.0, -3.0), BandGainOp(0.0, 1.0, 100.0, 200.0, -3.0)])
    audit = render(p, str(tmp_path / "dst.wav"))
    assert audit["chained"] is True
    assert [s["op"]["kind"] for s in audit["stages"]] == ["band_gain", "gain"]


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
