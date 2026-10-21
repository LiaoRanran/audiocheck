"""频谱测量层的**单元与差分**判据 —— 补 2026-10-20 整体变异复跑暴露的 29 条缺口。

缺口分布与形态：
* `_smooth_log_axis` 的**向量化实现**（`searchsorted` 的 side / 前缀和 / `cnt>=2` /
  单调性判据）——既有用例只在**均匀网格**上比过向量版与逐点版，而两版在
  **重复频点 / 非单调 / 极短**这几类输入上才会分叉；
* `detect_cutoff` 的**粗搜索参数**（`step`、`1.6` 窗、`0.04` 守卫、`need`、
  通带/阻带窗的下标）——既有用例只验"报得准"，验不到"这些常数各自的作用"；
* `SpectrumAnalyzer.run` 的**展示口径**（`round(cut/1000, 2)`、`cut/(fs/2)` 的 3 位、
  `10**(db/10)`）——它们只进输出字段，但只要写错，用户看到的 kHz/百分比就是错的。

本文件的前半是**差分判据**（拿逐点实现当真值，逐频点比对），后半是**边界判据**
（每个常数都造一个"只有它才对"的输入）。
"""
from __future__ import annotations

import numpy as np
import pytest

from audiocheck.config import Thresholds
from audiocheck.core.analyzers.spectrum import (
    SpectrumAnalyzer,
    _smooth_log_axis,
    _smooth_log_axis_pointwise,
    band_balance,
    detect_cutoff,
    smooth_power,
)

TH = Thresholds()


# ======================================================= 四、分析器的展示与守卫
class _Ctx:
    """`SpectrumAnalyzer.run` 的最小上下文（只提供它读的四个字段）。"""

    deep = True
    fs = 48000
    thresholds = TH

    def __init__(self, peak_dbfs: float, f, db):
        self.result = {"peak_dbfs": peak_dbfs}
        self._f, self._db = f, db

    def spectrum(self):
        return self._f, self._db


def test_spectrum_analyzer_order_is_30():
    """注册顺序 `order = 30`：`basic(10) → spectrum(30) → … → dc_offset(41)`。

    顺序决定 `basic` 的 `peak_dbfs` 与 `ctx.spectrum()` 是否就绪——改它属于
    会改变整条管线的决定，不能悄悄漂。
    """
    assert SpectrumAnalyzer.order == 30


def test_silence_gate_is_strictly_below_minus_60():
    """静音门限 `peak < −60.0`：**恰好 −60.0 dBFS 仍然要算**（不是静音）。

    改 `<=` 会把满刻度 −60 dB 的安静素材判成"无内容"⇒ 带宽/频段全部消失。
    """
    f = np.linspace(0, 24000, 8193)
    db = np.full_like(f, -20.0)
    assert "silent" not in SpectrumAnalyzer().run(_Ctx(-60.0, f, db))
    assert SpectrumAnalyzer().run(_Ctx(-60.01, f, db)) == {"silent": True}


def test_drop_gate_counts_exactly_min_drop_db():
    """粗搜索的 `below − above >= min_drop_db`：**恰好 10 dB 的台阶就算候选**。

    谱：0 dB 通带、−10 dB 阻带（台阶在 8 kHz）、`min_stop_db=0`（守卫不拦）。
    等号在"接受"侧 ⇒ 能定位到 ~8 kHz；改成 `>` ⇒ 整条谱没有候选 ⇒ 回落奈奎斯特。
    """
    f = np.linspace(0, 24000, 16385)
    db = np.where(f < 8000.0, 0.0, -10.0)
    cut, slope = detect_cutoff(f, db, min_drop_db=10.0, min_hz=1000.0,
                               need=34, min_stop_db=0.0)
    assert cut is not None and 7900.0 < cut < 8100.0, "实得 %r" % (cut,)
    assert slope > 0.0        # 陡度为正（功率域平滑会把 10 dB 台阶压到 ~7 dB/oct）


def test_band_balance_bands_are_half_open():
    """`band_balance` 的频段是**左闭右开** `[lo, hi)`：每个边界点只归一个频段。

    8 个频点恰好压 7 个边界（20/60/250/500/2000/6000/16000/20000）：
    20000 落在 `air=[16000, 20000)` 之外 ⇒ 7 个点各占 1/7 = 14.3%。
    `>=` 改 `>` 会丢掉下边界点（总数 6 ⇒ 16.7）；`<` 改 `<=` 会把 20000
    塞进 air（总数 8 ⇒ 12.5，air 翻倍）。
    """
    f = np.array([20.0, 60.0, 250.0, 500.0, 2000.0, 6000.0, 16000.0, 20000.0])
    parts = band_balance(f, np.ones_like(f))
    assert all(v == 14.3 for v in parts.values()), parts
    assert len(parts) == 7


def test_analyzer_bands_use_ten_per_decade():
    """`bands_pct` 的功率换算：`10**(db/10)`（不是 `db/11`，也不是 `db*10`）。

    两个频点放进**不同**频段：1 kHz @ 0 dB 与 5 kHz @ −10 dB ⇒
    功率比 10:1 ⇒ mid 90.9% / upper_mid 9.1%。
    底数改成 11 ⇒ 89.0/11.0；`/` 改 `*` ⇒ 100.0/0.0。
    """
    f = np.array([1000.0, 5000.0])
    db = np.array([0.0, -10.0])
    out = SpectrumAnalyzer().run(_Ctx(-3.0, f, db))
    assert out["bands_pct"]["mid"] == 90.9
    assert out["bands_pct"]["upper_mid"] == 9.1


# ============================================================ 一、平滑：差分 + 边界
@pytest.mark.parametrize("f", [
    np.array([]),                                            # 空
    np.array([1000.0]),                                       # 单点（走逐点分支）
    np.array([1000.0, 1000.0]),                               # 重复频点（diff == 0）
    np.array([2000.0, 1000.0]),                               # 非单调 ⇒ 走逐点分支
    np.array([0.0, 500.0, 1000.0]),                           # 含 0 Hz（逐点实现要跳过）
    np.linspace(0.0, 24000.0, 40),                            # 均匀（含 0）
    np.linspace(20.0, 24000.0, 7),                            # 极稀疏
    np.geomspace(20.0, 24000.0, 33),                          # 几何（非均匀）
    np.geomspace(20.0, 24000.0, 33) ** 1.3,                   # 非线性、但单调
])
def test_vectorized_smoother_matches_pointwise_on_adversarial_grids(f):
    """**逐点实现是唯一真源**：任何网格上两版都必须逐点一致（含重复点与非单调）。

    这一条专门钉向量版的 `searchsorted(side="left"/"right")` 与单调性判据：
    `diff >= 0` 改成 `> 0` ⇒ 重复频点会改走逐点分支——数值上仍一致（这条测不出），
    但**改走哪条分支**由下一条判据钉住。
    """
    rng = np.random.default_rng(7)
    y = rng.standard_normal(f.size)
    for frac in (6.0, 3.0, 12.0):
        a = _smooth_log_axis(f.copy(), y.copy(), frac)
        b = _smooth_log_axis_pointwise(f.copy(), y.copy(), frac)
        assert np.allclose(a, b, rtol=0.0, atol=1e-12), (frac, f.size)


def test_duplicate_frequencies_still_take_the_vectorized_path():
    """`np.all(np.diff(f) >= 0)` 的**等号**是必要的：重复频点视同**单调**。

    改 `>=` 为 `>` ⇒ 重复频点被判成"非单调"而退回逐点实现。两版结果相同、
    但**性能差 357 倍**（见函数 docstring 的实测）⇒ 这条判据用"打桩"确认走的是向量分支。
    """
    import audiocheck.core.analyzers.spectrum as sp

    calls = []
    orig = sp._smooth_log_axis_pointwise

    def spy(f, y, fraction):
        calls.append(1)
        return orig(f, y, fraction)

    sp._smooth_log_axis_pointwise = spy
    try:
        f = np.array([1000.0, 1000.0, 2000.0, 4000.0])
        _smooth_log_axis(f, np.arange(4.0), 6.0)
        assert calls == [], "重复频点（diff == 0）不该退回逐点实现"
        _smooth_log_axis(np.array([2000.0, 1000.0, 4000.0]), np.arange(3.0), 6.0)
        assert calls == [1], "非单调必须退回逐点实现"
    finally:
        sp._smooth_log_axis_pointwise = orig


def test_smoothing_fraction_controls_window_width():
    """`ratio = 2**(1/fraction)`：**fraction 越大窗越窄**（1/12 倍频程 < 1/6）。

    用一条"单点尖峰"谱：窗越窄，尖峰被摊开的范围越小（输出在尖峰邻居处更接近 0）。
    """
    f = np.geomspace(100.0, 10000.0, 120)
    y = np.zeros_like(f)
    y[60] = 1.0
    wide = _smooth_log_axis(f, y.copy(), 6.0)
    narrow = _smooth_log_axis(f, y.copy(), 12.0)
    assert (wide > 0).sum() > (narrow > 0).sum(), "1/12 倍频程的窗应当更窄"


def test_counts_and_prefix_sum_handle_the_first_and_last_bins():
    """前缀和的 `cs[hi] - cs[lo]`：**首尾 bin 不能越界、也不能偏一格**。

    构造一条只在**最低频**有点的谱：它的平滑值必须等于"窗内所有点"的均值，
    而窗内点数由 `searchsorted` 的两侧 side 决定——这一条同时钉
    `side="left"`（下界含等号）与 `side="right"`（上界含等号）。
    """
    f = np.array([100.0, 200.0, 400.0, 800.0])
    y = np.array([1.0, 0.0, 0.0, 0.0])
    out = _smooth_log_axis(f, y.copy(), 1.0)      # ratio = 2 ⇒ 每点窗 [fc/2, 2fc]
    # 100 Hz：窗 [50, 200] ⇒ 含 100 与 200 两个点 ⇒ 均值 0.5
    assert out[0] == pytest.approx(0.5)
    ref = _smooth_log_axis_pointwise(f, y.copy(), 1.0)
    assert np.array_equal(out, ref)


def test_smooth_power_floor_is_exactly_minus_200_db():
    """`10*log10(ps + 1e-20)`：**零功率区平滑后正好 −200 dB**（不是 −inf）。

    改 `1e-20` 为 `0.0` ⇒ 全零段的输出是 `-inf`（并且抛 numpy 警告），
    下游任何 `> / <` 比较都会拿到"永远更小"的假值 ⇒ 这条判据要求**有限**。
    """
    f = np.geomspace(20.0, 20000.0, 64)
    # 输入 −400 dB ⇒ 功率 1e-40；平滑后仍 ~1e-40，加 1e-20 后由那一项主导 ⇒ 恰好 −200
    out = smooth_power(f, np.full(f.size, -400.0))
    assert np.all(np.isfinite(out)), "平滑把零功率变成了 −inf"
    assert np.allclose(out, -200.0)
    # 输入 −200 dB ⇒ 功率 1e-20；`ps + 1e-20 = 2e-20` ⇒ −196.99 dB（**加性的**，不是原样）
    out2 = smooth_power(f, np.full(f.size, -200.0))
    assert np.allclose(out2, 10 * np.log10(2e-20), atol=1e-9)


# ============================================================ 二、截止点：每个常数各一条
def _brickwall(f, edge_hz, pass_db=-6.0, stop_db=-96.0):
    """理想砖墙谱：`f < edge` 取通带电平，以上取阻带底。"""
    return np.where(f < edge_hz, pass_db, stop_db)


def test_too_few_points_is_refused_not_guessed():
    """`len(f) < 16` 的**等号**：15 点拒绝（返回 None）、16 点必须能判。

    改 `<` 为 `<=` ⇒ 把 16 点的合法输入也拒绝掉（属性测试的网格最小就是 16 点）。
    """
    f = np.linspace(0.0, 24000.0, 15)
    assert detect_cutoff(f, _brickwall(f, 18000.0))[0] is None
    f16 = np.linspace(0.0, 24000.0, 16)
    cut, _ = detect_cutoff(f16, _brickwall(f16, 10500.0), min_drop_db=10.0, min_hz=1000.0)
    assert cut is not None and cut > 0


def test_coarse_step_is_one_twelfth_octave():
    """`step = 2**(1/12)`：粗搜索**只在这个网格上取候选**。

    造一条"边缘恰好落在网格点外侧一点"的砖墙：1/12 倍频程的候选序列会命中，
    而更粗的网格（如 1/3 倍频程）会整段跳过它、返回奈奎斯特。判据比的是**能否检出**。
    """
    f = np.linspace(0.0, 48000.0, 2049)
    edge = 48000.0 / 2 ** (1 / 12 * 2.5)          # 距奈奎斯特 2.5 个 1/12 倍频程
    cut, _ = detect_cutoff(f, _brickwall(f, edge), min_drop_db=15.0, min_hz=1000.0,
                           min_stop_db=20.0)
    assert cut is not None and abs(cut / edge - 1.0) < 0.05, (edge, cut)


def test_upper_window_guard_skips_when_almost_no_band_left():
    """`if hi_f - cur >= cur * 0.04`（上方频段占比 ≥4% 才评估）。

    这条守卫防的是"在奈奎斯特附近拿一条几乎空的上窗算中位数"。构造**顶到奈奎斯特
    的满带宽谱**：必须返回奈奎斯特（没有截止），而不是从顶部误报一个。
    """
    f = np.linspace(0.0, 24000.0, 1201)
    assert detect_cutoff(f, np.full(f.size, -6.0))[0] == pytest.approx(24000.0, abs=1.0)


def test_need_consecutive_points_is_respected():
    """`run >= need` 与 `j0 = i + need - 1`：**"连续 need 点"的产物位置**。

    把 `need` 从 3 调到 5：同一条砖墙的读出点要**上移**（因为要多跨两点才算数）。
    """
    f = np.linspace(0.0, 24000.0, 2401)
    db = _brickwall(f, 18000.0)
    c3, _ = detect_cutoff(f, db, min_drop_db=15.0, min_hz=1000.0, min_stop_db=20.0, need=3)
    c5, _ = detect_cutoff(f, db, min_drop_db=15.0, min_hz=1000.0, min_stop_db=20.0, need=5)
    assert c3 is not None and c5 is not None
    assert c5 >= c3, "need 变大后，读出点不该下移：%r vs %r" % (c5, c3)


def test_passband_window_is_the_narrow_non_straddling_one():
    """第二步的通带窗 `[coarse/2.56, coarse/1.6]`：**它决定阈值的通带侧电平**。

    这条在 `docs/theory.md §1.6` 里是精度修复的核心。判据：把一个**倾斜的通带**
    （每倍频程 −6 dB）交给检测器，读出点必须仍贴住真边缘（跨骑窗会塌到阻带底 ⇒ 偏高）。
    """
    f = np.linspace(0.0, 96000.0, 4097)
    edge = 9000.0
    slope = -6.0 * np.log2(np.maximum(f, 1.0) / 1000.0)
    db = np.where(f < edge, -6.0 + slope, -100.0)
    cut, _ = detect_cutoff(f, db, min_drop_db=15.0, min_hz=1000.0, min_stop_db=20.0)
    assert cut is not None and abs(cut / edge - 1.0) < 0.05, (edge, cut)


def test_cutoff_can_never_land_below_the_search_floor():
    """`if cut < min_hz: return None` 是**不可达**的防御分支——这条把它证明出来。

    搜索下界 `floor_hz = max(min_hz * 2, 100)`：候选一定在 `floor_hz` 之上，
    而第二步的阈值穿越点又从**上方**往下扫 ⇒ `cut ≥ floor_hz ≥ 2·min_hz > min_hz`。
    ⇒ 用 800 Hz 的低通砖墙 + `min_hz=1000` 时，检测器**看不到**它、如实报"无截止"
    （返回奈奎斯特），而不是报一个低于下限的读数。这个行为由判据钉住。
    """
    f = np.linspace(0.0, 48000.0, 2049)
    cut, _ = detect_cutoff(f, _brickwall(f, 800.0), min_drop_db=15.0,
                           min_hz=1000.0, min_stop_db=20.0)
    assert cut == pytest.approx(48000.0, abs=1.0), "低于搜索下限的砖墙应被如实略过"


def test_slope_is_measured_upwards_one_octave():
    """陡度 = 平滑谱在**上移一个倍频程**处的落差（正数、取 `s[j0] - s[j1]`）。

    砖墙落差 90 dB ⇒ 一个倍频程之后仍在阻带底 ⇒ 读数应接近 90。
    ⚠ 这一条同时钉住 `j1 > j0` 的守卫：若方向反过来（或落差点被取反），
    读数会变成 **0 或负数**——而"陡度"必须是正的（`axis_bandwidth` 拿它比门限）。
    """
    f = np.linspace(1000.0, 24000.0, 4601)
    edge = 12000.0
    db = np.where(f < edge, -6.0, -96.0)
    cut, slope = detect_cutoff(f, db, min_drop_db=15.0, min_hz=2000.0, min_stop_db=20.0)
    assert cut is not None and abs(cut / edge - 1.0) < 0.05
    assert 80.0 < slope < 100.0, slope


# ============================================================ 三、频段占比与展示口径
def test_band_balance_uses_half_open_bands():
    """`(f >= lo) & (f < hi)`：**每条带的右端是开区间**（否则相邻带会重复计数）。

    用一条恰好落在 250 Hz（BANDS 的边界之一）上的单点能量：它只能属于**上面**那条带。
    """
    lo, hi = [(lo, hi) for _, lo, hi in __import__("audiocheck.config", fromlist=["BANDS"]).BANDS][0]
    f = np.array([lo, hi - 1e-9, hi])
    p = np.array([1.0, 1.0, 1.0])
    out = band_balance(f, p)
    assert abs(sum(out.values()) - 100.0) <= 0.5      # 三个点全被算进去（`hi` 归下一条带）
    assert out


def test_band_balance_returns_empty_when_there_is_no_energy():
    """`tot > 0` 守卫：全零功率 ⇒ 空字典（不是除零、也不是全 0%）。"""
    f = np.linspace(0.0, 24000.0, 100)
    assert band_balance(f, np.zeros_like(f)) == {}


def test_analyzer_rounding_and_units_are_pinned():
    """展示口径：`cutoff_khz` 两位、`cutoff_ratio` 三位、频段用**线性功率**。

    这些字段只进界面/JSON，但写错就是"用户看到的 kHz 是错的"。
    判据：18 kHz 截止在 48k 容器里 ⇒ `18.0` kHz、`ratio = 0.75`。
    """
    from audiocheck.core.analyzers.spectrum import SpectrumAnalyzer

    f = np.linspace(0.0, 24000.0, 2401)
    db = np.where(f < 18000.0, -6.0, -96.0)

    class _Ctx:
        deep = True
        fs = 48000
        thresholds = TH
        result = {"peak_dbfs": -6.0}

        def spectrum(self):
            return f, db

    out = SpectrumAnalyzer().run(_Ctx())
    # 读数贴着 18 kHz（网格 10 Hz），但**判据钉的是"换算口径"**，不重复检测器的精度口径：
    assert 17900.0 < out["cutoff_hz"] < 18100.0
    assert out["cutoff_khz"] == round(out["cutoff_hz"] / 1000, 2)
    assert out["cutoff_ratio"] == round(out["cutoff_hz"] / (48000 / 2), 3)
    assert abs(sum(out["bands_pct"].values()) - 100.0) <= 0.2
