"""嗡自动检测的判据：**稳态**把嗡与乐音分开（不是谐波族）。

真值来自合成语料：嗡是我们叠上去的（频率/电平已知），乐音对照是衰减贝斯音
（55/41.2/82.4 Hz，含 6 次谐波 + 包络 + 揉弦）。三个门限都落在实测空隙里
（见 `core/analyzers/hum.py` 的 docstring 表）。
"""
from __future__ import annotations

import numpy as np
import pytest

from audiocheck.config import Thresholds
from audiocheck.core.analyzers.hum import detect_hum
from audiocheck.core.edit.corpus import band_limited_noise

FS = 48000


def _hum(clean: np.ndarray, f0: float, rel_db: float, fs: int = FS, seed: int = 7) -> np.ndarray:
    n = clean.shape[0]
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    h = np.zeros(n)
    for k in (1, 2, 3):
        h += np.sin(2 * np.pi * f0 * k * t + rng.uniform(0, 2 * np.pi)) / k
    h *= (10 ** (rel_db / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    return clean + h[:, None]


def _bass(f0: float, fs: int = FS, seconds: float = 6.0, decay_s: float = 0.35,
          amp: float = 0.5, seed: int = 3) -> np.ndarray:
    """衰减贝斯音：6 次谐波 + 指数包络 + 揉弦；每 1.5 s 重新起音（有"音符"在弹）。"""
    n = int(seconds * fs)
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    vib = 1.0 + 0.003 * np.sin(2 * np.pi * 5.5 * t)
    phase = 2 * np.pi * np.cumsum(f0 * vib) / fs
    x = np.zeros(n)
    for k in range(1, 7):
        x += (1.0 / k) * np.sin(k * phase + rng.uniform(0, 2 * np.pi))
    env = np.exp(-t / decay_s)
    x = x / float(np.abs(x * env).max()) * amp * env
    gate = (np.floor(t / 1.5) % 2 == 0).astype(float)
    gate = np.convolve(gate, np.hanning(256) / np.hanning(256).sum(), mode="same")
    return (x * gate)[:, None]


def test_detects_mains_hum_on_noise():
    """带限白噪 + 50 Hz 嗡（−20 dB）⇒ 检出 f0=50（±0.2），三项证据都在门限之上。"""
    x = _hum(band_limited_noise(6.0, FS, 18000.0, amp=0.5), 50.0, -20.0)
    r = detect_hum(x, FS)
    assert r["accepted"] is True
    assert abs(r["f0_hz"] - 50.0) <= 0.2, "检出 %s" % r["f0_hz"]
    assert min(r["excess_db"]) >= 4.0
    assert max(r["stability_db"]) <= 30.0
    assert min(r["quiet_excess_db"]) >= 0.0


def test_detects_60hz_hum_at_minus_30db():
    """60 Hz、−30 dB（弱嗡）也要检出——这是实测表里最不利的一档。"""
    x = _hum(band_limited_noise(6.0, FS, 18000.0, amp=0.5), 60.0, -30.0)
    r = detect_hum(x, FS)
    assert r["accepted"] is True
    assert abs(r["f0_hz"] - 60.0) <= 0.2, "检出 %s" % r["f0_hz"]


def test_refuses_clean_noise():
    """无嗡素材 ⇒ 必须拒绝（白噪的 excess 只有 ~4 dB，低于 4 的门限）。"""
    r = detect_hum(band_limited_noise(6.0, FS, 18000.0, amp=0.5), FS)
    assert r["accepted"] is False
    assert "证据不足" in r["reason"]


@pytest.mark.parametrize("f0", [55.0, 41.2, 82.4])
def test_refuses_a_bass_note(f0):
    """★ **乐音对照**：55 Hz 贝斯的 excess 比嗡还高（34.7 dB），但它是**非稳态**的。

    帧间稳定性 500+ dB（有起音/衰减/静音），安静帧 excess 是负的 ⇒ 两项都把它挡掉。
    这条是"为什么不能只看谐波族"的判据。
    """
    r = detect_hum(_bass(f0), FS)
    assert r["accepted"] is False, "把 %.1f Hz 的贝斯当成了嗡（f0=%s）" % (f0, r["f0_hz"])
    assert max(r["stability_db"]) > 100.0


def test_detects_hum_under_a_bass_note():
    """贝斯在弹、嗡也在：判定取**稳态线**（50 Hz），不是贝斯的基频。"""
    r = detect_hum(_hum(_bass(55.0), 50.0, -30.0), FS)
    assert r["accepted"] is True
    assert abs(r["f0_hz"] - 50.0) <= 0.3, "检出 %s" % r["f0_hz"]


def test_short_or_unsupported_material_is_refused():
    """太短 / 采样率放不下 3 次谐波 ⇒ 拒绝并说明原因（不猜）。"""
    short = detect_hum(band_limited_noise(1.0, FS, 18000.0), FS)
    assert short["accepted"] is False and "太短" in short["reason"]
    low_fs = detect_hum(band_limited_noise(3.0, 300, 100.0), 300)
    assert low_fs["accepted"] is False and "谐波" in low_fs["reason"]


# ============================================================ 打桩：直接喂谱图
# 上面 8 条钉的是"端到端结论"；内部的窗口/分位/插值算术必须用**可控谱图**钉，
# 否则"能在合成素材上对"不代表那几行没写错（变异复跑第一次实测 29/80）。
import audiocheck.core.analyzers.hum as hum  # noqa: E402

DF = 48000.0 / 16384.0                      # 2.9297 Hz


class _StubSignal:
    """只替换 `hum.signal`，不碰 scipy 全局（别的测试还要用真的 stft）。"""

    def __init__(self, f, mag_db, records=None):
        self.f, self.mag_db, self.records = f, mag_db, records

    def stft(self, _x, fs=None, nperseg=None, noverlap=None, window=None,
             boundary=None, padded=None):
        if self.records is not None:
            self.records.append({"fs": fs, "nperseg": nperseg, "noverlap": noverlap})
        Z = (10.0 ** (np.asarray(self.mag_db, dtype=np.float64) / 20.0)).astype(np.complex128)
        t = np.arange(np.asarray(self.mag_db).shape[1], dtype=np.float64) * 0.1
        return np.asarray(self.f, dtype=np.float64), t, Z


def _freqs(n=8193):
    return np.arange(n) * DF


def _flat(mag: float, bins: int, frames: int) -> np.ndarray:
    return np.full((bins, frames), mag)


def _lines_mag(frames: int, level=-56.0, base=-80.0, floor_bin=-60.0) -> np.ndarray:
    """三条线（j=17/34/51 @50 Hz 家族）都压在 `level`，邻域压在 `floor_bin`。

    ⚠ 第 1 次谐波的邻域**两端取值不同**（j−3 = −70、j+3 = −30 ⇒ 中位数 **−50**），
    且 j−4 = −40、j+4 = −80：这样"半宽 3→4 / 端点 +1 / `j−half` 变 `j+half`"
    三种改动都会移动中位数（对称时它们全都改不动，判据抓不住）。
    """
    mag = _flat(base, len(_freqs()), frames)
    for lo, hi in ((31, 37), (48, 54)):
        mag[lo] = mag[hi] = floor_bin
    mag[13] = -40.0
    mag[14] = -70.0
    mag[20] = -30.0
    mag[21] = -80.0
    for j in (17, 34, 51):
        mag[j] = level
    return mag


def test_lines_has_exactly_three_harmonics():
    """`range(1, K+1)` = 1/2/3 三条线（`K+1 → K−1` 会只剩两条，accept 也可能通过）。"""
    ev = hum._lines(_lines_mag(20), _freqs(), 49.8046875)
    assert len(ev) == 3


def test_lines_floor_is_the_asymmetric_neighbourhood_median():
    """邻域两格（j−3 = −70、j+3 = −30）的中位数 = **−50**；端点/半宽改动都会移动它。

    原实现 excess = 30.0；实测 `j−half`→`j+half` ⇒ **10.0**、半宽 3→4 ⇒ **50.0**、
    `j+3`→`j+4` ⇒ **50.0**（都变了，说明这三处都被判据钉住）。
    """
    ev = hum._lines(_lines_mag(20, level=-20.0), _freqs(), 49.8046875)
    assert ev[0][0] == pytest.approx(30.0, abs=1e-6)
    assert ev[0][2] == pytest.approx(30.0, abs=1e-6)


def test_lines_guard_sentinels_cover_all_three_fields():
    """哨兵是 (−999, 999, −999)：**三个字段都要钉**（只钉两个会漏掉安静的 −999）。"""
    ev = hum._lines(_flat(-80.0, 8193, 5), _freqs(), 0.0)
    assert ev[0] == (-999.0, 999.0, -999.0)


def test_peak_band_edges_return_none():
    """`j <= 0 or j >= len−1` 两侧：峰值落在带内最低格 / 最高格 ⇒ 都返回 None。

    `<=`→`<` 会放过 j=0（`M[−1]` 绕回），`>=`→`>` 会放过 j=len−1（`M[j+1]` 越界）。
    """
    f = np.array([44.0, 50.0, 55.0, 60.0, 66.0])
    M = np.array([30.0, 10.0, 5.0, 0.0, -5.0])      # 峰在 j=0
    assert hum._peak_hz(f, M) is None
    M2 = np.array([-5.0, 0.0, 5.0, 10.0, 30.0])     # 峰在 j=len−1
    assert hum._peak_hz(f, M2) is None


def test_peak_band_needs_three_bins():
    """`band.size < 3` 拒绝；**恰好 3 个频点要放行**（等号在"放行"侧）。"""
    f = np.array([0.0, 45.0, 50.0, 66.0, 100.0])     # [44,66] 内 3 个
    M = np.array([-30.0, 0.0, 20.0, 10.0, -20.0])
    assert hum._peak_hz(f, M) is not None
    f2 = np.array([0.0, 50.0, 66.0, 100.0])          # 内 2 个
    assert hum._peak_hz(f2, np.array([-30.0, 20.0, 10.0, -20.0])) is None


def test_peak_band_ignores_content_below_44hz():
    """搜索带下界 44 Hz：**10 Hz 处更强的线不许被选中**（`f >= lo` 的 `-`→`+` 会放开）。"""
    f = _freqs()
    M = _flat(-80.0, len(f), 1)[:, 0]
    j10 = int(np.argmin(np.abs(f - 10.0)))
    j50 = int(np.argmin(np.abs(f - 50.0)))
    M[j10 - 1:j10 + 2] = 40.0
    M[j50 - 1:j50 + 2] = 10.0
    assert hum._peak_hz(f, M) == pytest.approx(j50 * DF, abs=DF)


def test_offset_grid_uses_the_bin_spacing():
    """`df = f[1] − f[0]`：网格带偏移（f[0]=100）时 `f[1]+f[0]` 会算成 ~203 Hz。

    偏移下 `half` 仍是 3（10/2.93）；用不对称邻域的中位数（−60）钉住。
    """
    f = 100.0 + np.arange(8193) * DF
    ev = hum._lines(_lines_mag(20, level=-20.0), f, 149.8046875)   # 第 49 格 ≈ 49.8+100
    assert ev[0][0] == pytest.approx(30.0, abs=1e-6)


def test_nperseg_floor_is_256(monkeypatch):
    """`max(256, …)` 的下限：fs=600 时缩放值只有 205 ⇒ 取 256（改 257 会露）。"""
    rec = []
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), _flat(-80.0, 8193, 20), rec))
    hum.detect_hum(np.zeros(600 * 3), 600)
    assert rec[-1]["nperseg"] == 256


def test_nyquist_guard_uses_66_not_99(monkeypatch):
    """`3×66 ≥ fs/2` 的 **66**：fs=590 时 198 < 295 ⇒ 放行；换成 99（297）就会被误拒。"""
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), _flat(-80.0, 8193, 20)))
    r = hum.detect_hum(np.zeros(590 * 3), 590)
    assert "谐波" not in r["reason"], r["reason"]


def test_min_frames_boundary_is_eight(monkeypatch):
    """帧数门限 `< 8`：**恰好 8 帧要放行**（`8→9` 或 `<→<=` 都会拒绝）。"""
    f = _freqs()
    monkeypatch.setattr(hum, "signal", _StubSignal(f, _flat(-80.0, len(f), 8)))
    assert "帧数" not in hum.detect_hum(np.zeros(2 * 48000), 48000)["reason"]
    monkeypatch.setattr(hum, "signal", _StubSignal(f, _flat(-80.0, len(f), 7)))
    assert "帧数" in hum.detect_hum(np.zeros(2 * 48000), 48000)["reason"]


def test_log_epsilon_keeps_zero_magnitude_finite(monkeypatch):
    """`20·log10(|Z| + 1e-30)`：|Z| = 1e-30（−600 dB 的格）时加性 epsilon 给出 −294.85 dB。

    改成 `−` ⇒ |Z|−1e-30 = 0 ⇒ −inf（中位数被污染）；改成 0 ⇒ 同样 −inf。
    线 −56 ⇒ excess = 238.85（若 floor 变 −inf 则 excess 变 inf）。
    """
    mag = _lines_mag(20, level=-56.0)
    mag[14] = mag[20] = -600.0
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), mag))
    r = hum.detect_hum(np.zeros(2 * 48000), 48000)
    assert r["excess_db"][0] == pytest.approx(-56.0 - 20.0 * np.log10(2e-30), abs=0.01)


def test_quiet_gate_counts_cells_at_the_floor(monkeypatch):
    """安静帧门限 `q >= 门限`：**恰好相等要算通过**（21/100 帧落到本底 ⇒ p20 = 本底）。"""
    mag = _lines_mag(100, level=-20.0)
    mag[17, :21] = -49.9                        # 21/100 帧只高本底 0.1 dB ⇒ quiet = 0.1
    for j in (34, 51):
        mag[j, :21] = -59.9                     # 只高本底 0.1 dB（quiet 同为 0.1）
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), mag))
    # 0.1 dB 经 20log10 往返后有 ~1e-15 的误差 ⇒ 门限取 0.09（仍算"含等号"这一侧）
    th = Thresholds(hum_min_quiet_excess_db=0.09, hum_max_stability_db=45.0)
    r = hum.detect_hum(np.zeros(2 * 48000), 48000, th)
    assert r["quiet_excess_db"][0] == pytest.approx(0.1, abs=1e-6)
    assert r["accepted"] is True
    assert hum.detect_hum(np.zeros(2 * 48000), 48000,
                          Thresholds(hum_min_quiet_excess_db=0.2,
                                     hum_max_stability_db=45.0))["accepted"] is False


def test_lines_windows_percentiles_and_signs(monkeypatch):
    """`_lines` 每一项都钉死：邻域窗 [j−3, j−2] ∪ [j+3, j+4]、中位数、p5/p95/p20，
    以及 `excess = 线 − 底` 的**方向**。

    造法：线电平从 −40 以 0.5 dB/帧升到 +9.5（**中位数 −15.25** 必须高于邻域里
    最响的 −30，否则峰 picking 会被邻域格抢走）；邻域两格 −70/−30（中位数 −50）。
    三项证据是算得出来的闭式：excess = 34.75；p95−p5 = 30.5；p20 = −30.1 ⇒ quiet 19.9。
    """
    mag = _lines_mag(100, level=-60.0)
    mag[17, :] = -40.0 + 0.5 * np.arange(100)
    mag[34, :] = mag[51, :] = -40.0            # 2/3 次谐波也要过安静帧门限（quiet 20）
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), mag))
    th = Thresholds(hum_max_stability_db=45.0, hum_min_quiet_excess_db=19.5)
    r = hum.detect_hum(np.zeros(2 * 48000), 48000, th)
    assert r["excess_db"][0] == pytest.approx(34.75, abs=0.01)
    assert r["quiet_excess_db"][0] == pytest.approx(19.90, abs=0.02)
    assert r["stability_db"][0] == pytest.approx(44.55, abs=0.02)
    assert r["accepted"] is True


def test_accept_gates_are_inclusive_at_the_boundary(monkeypatch):
    """`_accept` 三个比较：excess `≥`、稳定 `≤`、安静 `≥` —— **都含等号**。"""
    mag = _lines_mag(30, level=-56.0)
    mag[14] = mag[20] = -60.0                  # 三个谐波的邻域都取 −60 ⇒ excess = 4.0 恰等
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), mag))
    assert hum.detect_hum(np.zeros(2 * 48000), 48000,
                          Thresholds(hum_min_excess_db=4.0))["accepted"] is True
    assert hum.detect_hum(np.zeros(2 * 48000), 48000,
                          Thresholds(hum_min_excess_db=4.1))["accepted"] is False
    assert hum.detect_hum(np.zeros(2 * 48000), 48000,
                          Thresholds(hum_min_quiet_excess_db=4.1))["accepted"] is False
    mag2 = _lines_mag(30, level=-56.0)
    mag2[14] = mag2[20] = -60.0                # 统一本底 −60 ⇒ excess = 4.0
    mag2[17, :3] = -86.0                       # 3/30 帧掉 30 dB ⇒ p5 = −86、p95 = −56
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), mag2))
    r = hum.detect_hum(np.zeros(2 * 48000), 48000,
                       Thresholds(hum_max_stability_db=30.0))
    assert r["stability_db"][0] == pytest.approx(30.0, abs=0.01)
    assert r["accepted"] is True
    assert hum.detect_hum(np.zeros(2 * 48000), 48000,
                          Thresholds(hum_max_stability_db=29.0))["accepted"] is False


def test_peak_interpolation_returns_the_parabolic_offset(monkeypatch):
    """`_peak_hz` 三点抛物线：y = [10, 20, 15] ⇒ δ = 0.5·(10−15)/(10−40+15) = 1/6。"""
    f = _freqs()
    M = _flat(-80.0, len(f), 1)[:, 0]
    j = int(np.argmin(np.abs(f - 50.0)))       # 17
    M[j - 1], M[j], M[j + 1] = 10.0, 20.0, 15.0
    assert hum._peak_hz(f, M) == pytest.approx((j + 0.5 * (10.0 - 15.0) / (10.0 - 40.0 + 15.0)) * DF)


def test_peak_search_band_is_44_to_66(monkeypatch):
    """搜索带 `[44, 66]`：把更强的线放在 70 Hz ⇒ 仍应报 ~50 Hz（70 在带外）。"""
    f = _freqs()
    M = _flat(-80.0, len(f), 1)[:, 0]
    j50 = int(np.argmin(np.abs(f - 50.0)))
    j70 = int(np.argmin(np.abs(f - 70.0)))
    M[j50 - 1:j50 + 2] = 10.0
    M[j70 - 1:j70 + 2] = 30.0
    assert hum._peak_hz(f, M) == pytest.approx(j50 * DF, abs=DF)


def test_lines_guard_sentinel_near_the_edges():
    """谱图边界守卫：`j < half+1 or j > len−half−2` ⇒ 哨兵 (−999/999)。

    上边界也要取等号：`j == len−half−2` 必须**放行**、`j == len−half−1` 必须触发
    （`>`→`>=` 与 `j−half`→`j+half` 都在这里分叉）。
    """
    f = _freqs()
    mag = _flat(-80.0, len(f), 5)
    half = max(2, int(round(hum.HALF_HZ / DF)))
    ev = hum._lines(mag, f, half * DF)
    assert ev[0][0] == -999.0 and ev[0][1] == 999.0
    assert hum._lines(mag, f, (half + 1) * DF)[0][0] != -999.0
    top = len(f) - half - 2
    assert hum._lines(mag, f, top * DF)[0][0] != -999.0
    assert hum._lines(mag, f, (top + 1) * DF)[0][0] == -999.0


def test_accept_comparisons_are_exact():
    """`_accept` 的三条比较在**精确值**上各测一次（不经 20log10 往返）。

    穿过 Z 的 dB 往返有 ~1e-15 误差，等号侧必须在**纯元组**上钉：
    `excess ≥`、`stability ≤`、`quiet ≥`；各差 1e-9 就必须拒绝。
    """
    th = Thresholds(hum_min_excess_db=4.0, hum_max_stability_db=30.0,
                    hum_min_quiet_excess_db=0.0)
    assert hum._accept([(4.0, 30.0, 0.0)] * 3, th) is True
    assert hum._accept([(4.0 - 1e-9, 30.0, 0.0)], th) is False
    assert hum._accept([(4.0, 30.0 + 1e-9, 0.0)], th) is False
    assert hum._accept([(4.0, 30.0, 0.0 - 1e-9)], th) is False


def test_scaling_keeps_the_frequency_grid(monkeypatch):
    """窗长按采样率缩放：48k→16384、24k→8192、8k→2731（频率格恒 2.93 Hz），帧移 1/4。"""
    rec = []
    monkeypatch.setattr(hum, "signal", _StubSignal(_freqs(), _flat(-80.0, 8193, 20), rec))
    for fs, seg, hop in ((48000, 16384, 4096), (24000, 8192, 2048), (8000, 2731, 682)):
        hum.detect_hum(np.zeros(fs * 3), fs)
        assert rec[-1]["nperseg"] == seg, rec[-1]
        assert rec[-1]["noverlap"] == seg - hop


def test_short_nyquist_and_frame_guards(monkeypatch):
    """三条早退：<2 s、3×66 ≥ fs/2、帧数 <8 —— 边界各取一次两侧。"""
    f = _freqs()
    monkeypatch.setattr(hum, "signal", _StubSignal(f, _flat(-80.0, len(f), 8)))
    assert "太短" in hum.detect_hum(np.zeros(2 * 48000 - 1), 48000)["reason"]
    assert "谐波" in hum.detect_hum(np.zeros(2 * 396), 396)["reason"]
    assert "谐波" not in hum.detect_hum(np.zeros(2 * 48000), 48000)["reason"]
    monkeypatch.setattr(hum, "signal", _StubSignal(f, _flat(-80.0, len(f), 7)))
    assert "帧数" in hum.detect_hum(np.zeros(2 * 48000), 48000)["reason"]


def test_output_rounding_and_df(monkeypatch):
    """`f0` 保留 **2** 位、`df_hz` 保留 **3** 位（2.9297 → 2.93）。"""
    f = _freqs()
    M = _flat(-80.0, len(f), 20)
    j = int(np.argmin(np.abs(f - 50.0)))
    M[14, :] = M[20, :] = -60.0
    M[j, :] = -30.0
    M[j - 1, 0] = M[j + 1, 0] = 0.0
    monkeypatch.setattr(hum, "signal", _StubSignal(f, M))
    r = hum.detect_hum(np.zeros(2 * 48000), 48000)
    assert r["df_hz"] == 2.93
    assert r["f0_hz"] == round(r["f0_hz"], 2)


def test_module_constants_are_the_documented_ones():
    """算法参数的**定义**也钉住（不是判据阈值，是口径）。"""
    assert hum.NPERSEG == 16384 and hum.HOP == 4096
    assert hum.K == 3 and hum.HALF_HZ == 10.0 and hum.MIN_SECONDS == 2.0
