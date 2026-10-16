"""属性测试（E1）：把**必须恒成立**的物理/数学不变量钉死。

为什么值得单独立一个文件
------------------------
举例测试只证明"我给的那几个点对"；属性测试证明"**任意**输入都对"，
并且在反例上 shrink 到最小可复现输入。本文件把四条不变量从手挑的
几个点扩展到随机输入空间。

⚠ 纪律（E1 提示词）：属性必须是**物理/数学上必须为真**的，否则测试只是
在描述当前实现（同义反复）。每条都注明依据：

  1. **幅度单调**：LUFS 定义为 `-0.691 + 10·log10(Σ K加权均方)`，
     K 加权是线性时不变算子 ⇒ 信号乘以 a 时读数恰好平移 `20·log10(a)`。
     a=0.5 ⇒ **−6.0206 LU**，与频率/波形无关。
  2. **采样率不变性**：同一物理内容（带限到 f_c）在 44.1k/48k/96k 下，
     截止点检测应给出一致结果（容差内）——这是"看内容不看容器"的前提。
  3. **窗长权衡**：STFT 窗长 N ⇒ Δf = fs/N、Δt = hop/fs，二者反向单调。
     这是时频不确定性原理的直接推论。
  4. **位深单调**：B bit PCM 的量化信噪比 ≈ `6.02·B + 1.76` dB，随 B 严格递增。
     这是位级判据（有效位深/升位检测）赖以成立的物理边界。

## 本文件发现并修复了一个真实缺陷
`detect_cutoff` 的粗搜索（自高向低取**第一个**满足 `min_drop_db` 的点）会在
**截止点约为奈奎斯特 10–25%** 时选中一个落在**过渡带**上的候选；随后的
`min_stop_db` 守卫否决它并**整体返回奈奎斯特** ⇒ 表现为"满带宽"假阴性。
实测命中率损失：44.1k 6/53、48k 6/53、96k 12/53（含 44.1k@16.5k 这种典型有损截止）。
修法：守卫不过就从候选**下方继续搜**（不回归——原本成功的输入其首个候选即过守卫）。
见 `test_low_cutoff_is_not_reported_as_nyquist` 与 CHANGELOG。
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np
import pytest
import soundfile as sf
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core import dsp as D  # noqa: E402
from audiocheck.core import views  # noqa: E402
from audiocheck.core.analyzers import bitdepth  # noqa: E402
from audiocheck.core.analyzers.spectrum import detect_cutoff  # noqa: E402

#: 属性测试里每个 example 都会写文件/做 FFT，远超 hypothesis 默认 200ms deadline。
_SLOW = settings(max_examples=12, deadline=None,
                 suppress_health_check=[HealthCheck.too_slow])

_TMP = tempfile.mkdtemp(prefix="ac_props_")


def _write_mono(x: np.ndarray, fs: int, name: str, subtype: str = "FLOAT") -> str:
    p = os.path.join(_TMP, name)
    sf.write(p, np.column_stack([x, x]).astype(np.float32), fs, subtype=subtype)
    return p


def _bandlimited(fs: int, hz: float, seconds: float = 3.0, seed: int = 3) -> np.ndarray:
    """白噪声硬切到 hz——"答案已知"的砖墙素材。"""
    n = int(fs * seconds)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(n, 1.0 / fs) > hz] = 0.0
    return np.fft.irfft(spec, n=n).astype(np.float32)


# --------------------------------------------------------------- 1. 幅度单调
@given(amp=st.floats(min_value=0.05, max_value=0.5),
       freq=st.floats(min_value=100.0, max_value=8000.0))
@_SLOW
def test_halving_amplitude_drops_about_6_02_lu(amp, freq):
    """任意频率 / 任意幅度的正弦：幅度减半 ⇒ LUFS 降 **6.0206 LU**。

    容差 0.15：`dsp.loudness` 的 lufs 字段 `round(...,1)`（两次舍入各 ≤0.05），
    是**显示口径**不是数学误差。底层 `_gated_loudness` 是精确的。
    """
    fs = 48000
    t = np.arange(int(fs * 1.5)) / fs
    tone = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)
    a = D.loudness(_write_mono(tone, fs, "loud_a.wav"), fs, 2, len(tone))["lufs"]
    b = D.loudness(_write_mono(tone * 0.5, fs, "loud_b.wav"), fs, 2, len(tone))["lufs"]
    assert a is not None and b is not None
    assert abs((a - b) - 6.0206) <= 0.15, "幅度减半的响度落差 %.4f LU（应 ≈6.0206）" % (a - b)


def test_scaling_law_is_exact_in_the_gating_math():
    """把显示舍入剥掉：底层门限积分在缩放下的落**恰好**是 20·log10(0.5)。

    直接喂 `dsp._gated_loudness`（标准门限积分的真实现）两组相差 4 倍
    （功率相差 2²）的块——证明"6.02 LU"来自数学而非巧合。
    """
    rng = np.random.default_rng(11)
    blk = np.abs(rng.standard_normal((200, 2))) + 0.5          # 恒正，模拟均方块
    l1 = D._gated_loudness(blk, D.REL_GATE_INT)
    l2 = D._gated_loudness(blk * 0.25, D.REL_GATE_INT)         # 幅度 ×0.5 ⇒ 功率 ×0.25
    assert abs((l1 - l2) - 20 * np.log10(2.0)) < 1e-9


# ------------------------------------------------------- 2. 采样率不变性
@given(cut=st.floats(min_value=12000.0, max_value=21000.0))
@settings(max_examples=14, deadline=None,
          suppress_health_check=[HealthCheck.too_slow])
def test_cutoff_consistent_across_sample_rates(cut):
    """同一带限内容（砖墙 @cut）在 44.1k/48k/96k 下的检出应一致。

    容差 15%（**实测标定**，非拍脑袋）：250 Hz 步长扫 [12000, 21000] ×
    {44.1k,48k,96k}，最大相对偏差 10.8%、最大跨率散布 10.2% ⇒ 取 15% 留 1.4× 余量。
    依据是 1/6 倍频程平滑（≈12% 量级）带来的 fs 相关系统偏差。

    ⚠ **本属性只覆盖 ≥12 kHz**：实测 9–12 kHz 段偏差可达 15%（96k 容器），
    更低截止的精度判据**未立**（如实登记，不假装覆盖）。
    ⚠ 这条在修复 `detect_cutoff` 粗搜索缺陷**之前会红**——它是发现那个缺陷的属性。
    """
    est = []
    for fs in (44100, 48000, 96000):
        x = _bandlimited(fs, cut)
        f, p = signal.welch(x, fs=fs, nperseg=16384, noverlap=8192, window="hann")
        db = 10 * np.log10(p + 1e-20)
        c, _ = detect_cutoff(f, db)
        assert c is not None, "fs=%d cut=%.0f 完全未检出" % (fs, cut)
        est.append(c)
    rel = [abs(c - cut) / cut for c in est]
    assert max(rel) <= 0.15, "检出偏离真值 >15%%：cut=%.0f 估=%s" % (cut, [round(c) for c in est])
    spread = (max(est) - min(est)) / cut
    assert spread <= 0.15, "跨采样率不一致 %.1f%%：cut=%.0f 估=%s" % (
        spread * 100, cut, [round(c) for c in est])


@pytest.mark.parametrize("fs,cut", [
    (44100, 16500), (44100, 15500), (44100, 9000),      # 44.1k 过渡带候选区
    (48000, 6000), (48000, 15000),                       # 48k
    (96000, 10000), (96000, 12000), (96000, 18350), (96000, 21000),   # 96k
])
def test_low_cutoff_is_not_reported_as_nyquist(fs, cut):
    """回归（E1 属性测试发现的缺陷）：这些 (fs, 截止) 曾返回**奈奎斯特**。

    缺陷根因：粗搜索选中过渡带候选 → `min_stop_db` 守卫否决 → 整体返回奈奎斯特
    ⇒ 内容被判"满带宽"（假阴性）。修法：守卫不过则从候选下方继续搜。
    本测试逐点钉住修复，防止回潮。
    """
    x = _bandlimited(fs, cut)
    f, p = signal.welch(x, fs=fs, nperseg=16384, noverlap=8192, window="hann")
    c, _ = detect_cutoff(f, 10 * np.log10(p + 1e-20))
    assert c is not None, "完全未检出"
    assert abs(c - fs / 2) > 1.0, "报成奈奎斯特（满带宽假阴性）"
    assert abs(c - cut) / cut <= 0.12, "检出 %.0f Hz，真值 %d Hz" % (c, cut)


# --------------------------------------------------------- 3. 窗长权衡
@given(pair=st.sampled_from([(2048, 4096), (2048, 8192), (4096, 8192)]))
@_SLOW
def test_longer_window_trades_time_for_frequency_resolution(pair):
    """窗越长 ⇒ 频率分辨率越好（Δf 降）、时间分辨率越差（Δt 升）。

    量的是 `views._stft_power` **真实现**输出的坐标轴间距——
    若有人把 hop 或频率轴写死，这条会红。
    """
    n_short, n_long = pair
    fs = 48000
    x = np.column_stack([np.random.default_rng(5).standard_normal(fs * 2)] * 2).astype(np.float32)
    out = {}
    for n in (n_short, n_long):
        f, t, _ = views._stft_power(x, fs, n, views._hop_for(n))
        out[n] = (float(f[1] - f[0]), float(t[1] - t[0]))
    df_s, dt_s = out[n_short]
    df_l, dt_l = out[n_long]
    assert df_l < df_s, "窗变长但 Δf 没变小：%.3f → %.3f" % (df_s, df_l)
    assert dt_l > dt_s, "窗变长但 Δt 没变大：%.4f → %.4f" % (dt_s, dt_l)


# --------------------------------------------------------- 4. 位深单调
@given(bits=st.integers(min_value=8, max_value=16))
@_SLOW
def test_quantization_snr_grows_with_bit_depth(bits):
    """B bit 量化满刻度正弦：SNR ≈ 6.02·B + 1.76 dB，且随 B 严格递增。

    这条量的是**物理定律本身**（位级判据的边界），不依赖项目代码；
    它守的是"我们假设的量化模型是对的"。
    """
    fs = 48000
    t = np.arange(fs) / fs
    x = np.sin(2 * np.pi * 997.0 * t)                 # 满刻度、非整数周期（避免量化误差相关）
    step = 2.0 / (2 ** bits)

    def snr_db(q):
        e = x - np.round(x / q) * q
        return 10 * np.log10(np.mean(x ** 2) / max(np.mean(e ** 2), 1e-30))

    s = snr_db(step)
    assert abs(s - (6.02 * bits + 1.76)) <= 1.5, "B=%d SNR=%.2f（理论 %.2f）" % (
        bits, s, 6.02 * bits + 1.76)
    assert snr_db(step / 2) > s, "位深 +1 但 SNR 没升"


@given(q=st.integers(min_value=16, max_value=22))
@_SLOW
def test_effective_bits_equals_content_quantization_depth(q):
    """内容量化到 q bit、装进 24bit 容器（低 24−q 位恒 0）⇒ 检出有效位 = q。

    ⚠ 写入必须走 **int32 左移对齐**（值 <<8 落在高 24 位）：`sf.write` 直接喂
    float 会因 libsndfile 用 `2²³−1` 缩放而把低位填满，检出恒为 24（实测）。
    """
    fs = 48000
    rng = np.random.default_rng(7)
    x = np.clip(rng.standard_normal(fs) * 0.5, -1.0, 1.0)
    step = 2.0 / (2 ** q)
    xq = np.round(x / step) * step
    xi = (np.round(xq * (2 ** 23)).astype(np.int64) * 256).astype(np.int32)
    p = os.path.join(_TMP, "bits_%02d.wav" % q)
    sf.write(p, np.column_stack([xi, xi]), fs, subtype="PCM_24")
    r = bitdepth.analyze(p, "PCM_24", 2)
    assert r["bit_effective"] == q, "q=%d 检出有效位 %s" % (q, r["bit_effective"])
