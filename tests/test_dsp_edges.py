"""`dsp.py` 的等号侧 / 数值守卫 / 口径判据（2026-10-21 变异缺口补全轮）。

背景：全量变异复跑后 `dsp.py` 留下 **116 条存活**（四个旧模块里最大的一块）。
本文件按函数逐组填：默认值、`1e-30` 类数值守卫、门限/分档的**等号侧**、舍入位数。

方法（与前几轮一致）：
  * 能用行为区分的就行为区分（合成素材 + 精确数值断言）；
  * 需要**精确控制中间量**的用打桩（`signal.stft` / `signal.lfilter` / `sf.read`）。
    打桩本身也是判据：它钉住"这条数学走哪条路径、用哪个窗口/哪个阈值"，
    以及"分母里的 epsilon 到底在守什么"。

⚠ 等号侧的写法：凡是断言 `== "某档"` 的地方，素材都取在**恰好等于门限**的位置
（如相关系数恰好 0.3、ρ 恰好 −0.9、能量占比恰好 1%），否则 `>` 与 `>=` 不可区分。
"""
from __future__ import annotations

import inspect

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from audiocheck.core import dsp

FS = 48000


# --------------------------------------------------------------- 门限积分响度
def test_gated_loudness_uses_10log10_and_relative_gate():
    """`lv = OFFSET + 10·log10(z)`；相对门限是 `均值 + rel_gate`。

    两块功率 1000 / 100（lv 29.31 / 19.31），均值 550 → rel = 16.71 ⇒ 两块都保留。
    把任一处的 `10` 改成 `11`：rel 变 19.45 ⇒ 只剩第一块 ⇒ 读数 29.31（不是 26.71）。
    """
    blk = np.array([[1000.0], [100.0]])
    got = dsp._gated_loudness(blk, -10.0)
    assert got == pytest.approx(dsp.OFFSET + 10 * np.log10(550.0 + 1e-30), abs=1e-12)
    assert got == pytest.approx(26.71262689494244, abs=1e-9)
    # 相对门限取极窄（-100）时两块全留 —— 均值口径的直接证据
    assert dsp._gated_loudness(blk, -100.0) == pytest.approx(26.71262689494244, abs=1e-9)


def test_loudness_chunk_default_is_one_megaframe():
    """默认读块 `1<<20` 帧：`loudness` 与 `true_peak_scan` 必须同一个量级口径。

    块长只影响**读**（内存峰值），不该影响读数；但它是每条分析都要付的代价，
    所以写死在签名里当契约。`true_peak_scan` / `true_peak` 用 `1<<21`（4× 过采样
    要留余量，单块上限更大是有意的）——三个默认值各自钉住。
    """
    assert inspect.signature(dsp.loudness).parameters["chunk"].default == 1 << 20
    assert inspect.signature(dsp.true_peak_scan).parameters["chunk"].default == 1 << 21
    assert inspect.signature(dsp.true_peak).parameters["chunk"].default == 1 << 21


def test_loudness_runs_when_hop_is_one_sample(tmp_path):
    """`hop = int(0.1·fs)` 恰为 **1** 时是**可算的**（只有 0 才必须拒绝）。

    fs=10 ⇒ hop=1 ⇒ 4 个采样正好 4 个 100 ms 块。守卫写成 `hop <= 1` 会把这个
    合法（虽然极端）的采样率直接判成"没法算"。
    """
    p = tmp_path / "hop1.wav"
    sf.write(str(p), np.zeros((4, 1), dtype=np.float32), 10)
    r = dsp.loudness(str(p), 10, 1, 4, true_peak_dbfs=None)
    assert r != {}, "hop == 1 是合法输入，不许被 `hop <= 1` 提前拒绝"
    assert "lufs" in r
    # 对照：fs=4 ⇒ hop=0 ⇒ 必须拒绝（否则 nsteps = total // 0 直接炸）
    assert dsp.loudness(str(p), 4, 1, 4) == {}


def test_loudness_needs_exactly_four_blocks(tmp_path):
    """`si < 4` 的等号侧：恰好 4 个 100 ms 块出结果，3 个（少一个采样）拒绝。

    19200 = 4 × 4800（48k 下 hop）⇒ 可算；19199 ⇒ `nsteps = 3` ⇒ `si = 3` ⇒ `{}`。
    `4` 改成 `5` 会让"恰好 4 块"的短素材**安静地丢掉全部读数**。
    """
    for n, has in ((19200, True), (19199, False)):
        p = tmp_path / ("b%d.wav" % n)
        sf.write(str(p), np.zeros((n, 1), dtype=np.float32), FS)
        r = dsp.loudness(str(p), FS, 1, n, true_peak_dbfs=None)
        assert (r != {}) is has, "%d 采样（%d 块）→ %r" % (n, n // 4800, r)


def test_loudness_is_invariant_to_read_chunk(tmp_path):
    """跨读块续接：`acc_n` 的取块算术必须与读块边界无关。

    素材在 **100000**（= chunk 边界）之后 800 采样处放一个突发——正好跨在
    "上一块的残余 + 这一块的开头"上。把 `hop - acc_n` 写成 `hop + acc_n` / 把
    `y.shape[0] - p` 写成 `y.shape[0] + p`，都会在这个续接点上把步长记错：
    前者让 `acc_n` 再也不会回到 hop（后面的块**全部丢弃** ⇒ `st_max` 变 None），
    后者把"只累加了 remaining 个采样"当成满块（该步电平被压低）。
    """
    n = 290_000
    t = np.arange(n) / FS
    x = (1e-3 * np.sin(2 * np.pi * 200 * t)).astype(np.float32)
    x[100_000:100_800] = (0.9 * np.sin(2 * np.pi * 1000 * np.arange(800) / FS)).astype(np.float32)
    p = tmp_path / "burst.wav"
    sf.write(str(p), np.column_stack([x, x]), FS, subtype="FLOAT")
    ref = dsp.loudness(str(p), FS, 2, n, true_peak_dbfs=None)          # 单块（无续接）
    got = dsp.loudness(str(p), FS, 2, n, chunk=100_000, true_peak_dbfs=None)
    assert ref["st_max"] is not None and got["st_max"] is not None, \
        "读块变小后短时块个数塌了（acc_n 续接算错）"
    assert got["lufs"] == pytest.approx(ref["lufs"], abs=0.05), \
        "读块 100000 与单块读数不一致：%s vs %s（突发跨在块边界上）" % (got["lufs"], ref["lufs"])


# --------------------------------------------------------------- 谱特征
def test_spectral_features_power_is_10_per_decade():
    """`p = 10^(db/10)`：0 dB 与 −10 dB 两点的质心必须落在 90.9 Hz（= 0.1/1.1×1000）。

    底数改成 `11`（或把 `/10` 写成 `*10`）都会让权重变形：前者质心 109.8，
    后者 1e-100 的权重把质心压到 ~0。
    """
    f = np.array([0.0, 1000.0])
    db = np.array([0.0, -10.0])
    feats = dsp.spectral_features(f, db)
    assert feats["centroid_hz"] == 90.9
    assert feats["rolloff85_hz"] == 0.0      # 累积到 0.85 时还在第一个频点
    assert feats["rolloff95_hz"] == 1000.0


def test_rolloff_points_are_85th_and_95th_percentile():
    """滚降点必须取 **0.85 / 0.95** 累积点：九等分能量 + 一个 1e-3 的尾巴。

    cum = [1/9.001 … 9/9.001, 1] ⇒ 0.85 落在下标 **7**、0.95 落在下标 **8**。
    把门限乘 1.5（1.275 / 1.425）会一路越过 1.0、被 `len(f)-1` 夹到下标 9。
    """
    f = np.arange(10, dtype=float)
    db = np.array([0.0] * 9 + [-30.0])
    feats = dsp.spectral_features(f, db)
    assert feats["rolloff85_hz"] == 7.0
    assert feats["rolloff95_hz"] == 8.0


def test_flatness_ignores_zero_power_bins():
    """全零功率的格子不进几何平均（`p[p > 0]`，不是 `>= 0`）。

    −inf dB ⇒ p = 0：两项里只有一项参与几何平均 ⇒ gm = 1、均值功率 = 0.5 ⇒ 平坦度 2.0。
    写成 `>=` 会把 log(0+1e-30) = −69 也算进去 ⇒ 平坦度塌到 ~1e-15。
    """
    feats = dsp.spectral_features(np.array([0.0, 1000.0]), np.array([0.0, -np.inf]))
    assert feats["flatness"] == 2.0


def test_flatness_epsilon_is_additive_not_subtractive():
    """平坦度里 `log(p + 1e-30)` 的 epsilon 是**加的**（p 可以小到 1e-40）。

    −400 dB 的单点：gm = 1e-40 + 1e-30 ≈ 1e-30，分母均值也是 1e-30 ⇒ 平坦度恰 1.0。
    改成 `-1e-30` ⇒ 9e-31/1e-30 = 0.8999；改成 `0.0` ⇒ 1e-40/1e-30 = 1e-10（舍入后 0.0）。
    """
    feats = dsp.spectral_features(np.array([0.0]), np.array([-400.0]))
    assert feats["flatness"] == 1.0


def test_flatness_is_rounded_to_five_digits():
    """展示口径：平坦度保留 **5** 位（0 dB 与 −3.0103 dB 两点 ⇒ 0.94280904…）。

    5 位 → 0.94281；6 位 → 0.942809。这是给人看的字段，位数漂了要能发现。
    """
    feats = dsp.spectral_features(np.array([0.0, 1000.0]),
                                  np.array([0.0, -3.010299956639812]))
    assert feats["flatness"] == 0.94281


def test_noise_floor_is_the_fifth_percentile():
    """`noise_floor_db` = 谱 dB 的**第 5 百分位**（不是第 6/10）。

    等差 0..78 dB 的 40 点：p5 = 1.95 → 1.9；p6 = 2.34 → 2.3。
    """
    f = np.linspace(0, 22050, 40)
    db = np.arange(40, dtype=float) * 2
    feats = dsp.spectral_features(f, db)
    assert feats["noise_floor_db"] == round(float(np.percentile(db, 5)), 1)
    assert round(float(np.percentile(db, 5)), 1) != round(float(np.percentile(db, 6)), 1), \
        "素材自身没有区分 5/6 百分位，判据无效"


# --------------------------------------------------------------- 立体声
def test_stereo_width_from_mid_side_energy():
    """`width_db = 10·log10(side²/mid²)`：L=1、R=0.5 ⇒ side/mid = 1/3 ⇒ −9.5 dB。

    同时钉住 M/S 的**减法定义**：把 `lc - rc` 写成 `lc + rc`（或反过来把 mid 写成
    side）会得到 +9.5 dB；把 `/2` 写成 `*2` 让比值差 4–16 倍。
    """
    n = 2048
    feats = dsp.stereo_features(np.ones(n), np.full(n, 0.5))
    assert feats["width_db"] == -9.5
    assert feats["corr"] == 1.0


def test_stereo_corr_formula_and_rounding():
    """`corr = mean(l·r) / sqrt(mean(l²)·mean(r²))`，展示保留 **3** 位。

    素材 L≡1、R 为 1/0 交替 ⇒ corr = 0.5/√0.5 = 0.70710678… → 0.707（4 位会变 0.7071）。
    分子写成 `l/r` 会因除零变成 nan；分母写成 `mean(r/2)` ⇒ corr = 1.0。
    """
    n = 2048
    lc = np.ones(n)
    rc = np.tile(np.array([1.0, 0.0]), n // 2)
    feats = dsp.stereo_features(lc, rc)
    assert feats["corr"] == 0.707


def test_stereo_corr_gates_are_exact_at_0_3_and_minus_0_1():
    """单声道兼容性分档的**等号侧**：corr 恰 0.3 / 恰 −0.1 都算"一般"。

    素材：20 采样周期里 13 个 +1、7 个 −1 ⇒ mean(l·r) = 6/20 = 0.3 **逐位等于字面量 0.3**
    （l² 与 r² 的均值都是 1）。`> 0.3` 改 `>=` 会把这一档判成"良好"。
    """
    n = 2040
    a = np.tile(np.array([1.0] * 13 + [-1.0] * 7), n // 20)
    ra = dsp.stereo_features(np.ones(n), a)
    assert ra["corr"] == 0.3 and ra["mono_compat"] == "一般"
    b = np.tile(np.array([1.0] * 9 + [-1.0] * 11), n // 20)
    rb = dsp.stereo_features(np.ones(n), b)
    assert rb["corr"] == -0.1 and rb["mono_compat"] == "一般"


def test_stereo_mono_signal_hits_the_epsilon_floor():
    """完全单声道（L≡R）时 side 恰为 0：宽度由 `+1e-30` 的 epsilon 决定 ⇒ −300 dB。

    `se + 1e-30` 写成 `se - 1e-30` ⇒ 比值变负 ⇒ log 得 nan；
    写成 `se + 0` ⇒ log10(0) = −inf。两者都不是"一个有限的 dB 读数"。
    """
    feats = dsp.stereo_features(np.ones(2048), np.ones(2048))
    assert feats["width_db"] == -300.0


def test_stereo_channel_cap_is_four_million_samples():
    """`4_000_000` 采样上限是**内存契约**（超长素材抽样后再算相关系数）。

    行为判据要 8M 采样的立体声（≈ 400 MB 峰值），在 CI 上不合算 ⇒ 这里钉源码常量；
    改上限会同时改变 `step` 与相关系数的抽样密度，属于会动读数的决定。
    """
    assert "4_000_000" in inspect.getsource(dsp.stereo_features)


# --------------------------------------------------------------- 相位场
def test_phase_field_default_hop_is_three_quarters_of_window():
    """默认 `hop = nperseg·3//4` ⇒ 4096 点窗、3072 点跳。

    8192 采样 ⇒ 4 帧、帧距 3072/fs。跳改成 `nperseg`（`3->4`）帧距变 4096/fs；
    改成 `nperseg/3//4` 帧数暴增。
    """
    f, t, rho = dsp.phase_field(np.zeros((8192, 2), dtype=np.float32), FS)
    assert len(t) == 4
    assert t[1] - t[0] == pytest.approx(3072 / FS)
    assert rho.shape[0] == len(f)


def test_phase_field_silence_is_neutral_zero_not_nan():
    """全零时 ρ 的分母是 0：靠 `+1e-30` 得 0（中性），不是 nan。

    去掉 epsilon ⇒ 0/0 = nan，`np.where(undef, 0, rho)` 也不会替换它
    （静音格的 `undef` 是 `0 < 0` = False）。
    """
    _f, _t, rho = dsp.phase_field(np.zeros((8192, 2), dtype=np.float32), FS)
    assert np.all(rho == 0.0)


def test_phase_field_undefined_side_threshold_is_30db():
    """弱侧比强侧低 **30 dB**（功率比 1e-3）时 ρ 无定义、置 0。

    取功率比 **5e-4**（−33 dB）：按 30 dB 判据 ⇒ 无定义（ρ=0）；
    按 45 dB 判据（`15^-3` = 2.96e-4）⇒ 有定义 ⇒ ρ≈+1。两边都实测过。
    """
    t = np.arange(FS) / FS
    base = np.sin(2 * np.pi * 1000 * t)

    def _rho(ratio):
        f, _t, rho = dsp.phase_field(np.column_stack([base, np.sqrt(ratio) * base]), FS)
        band = (f > 900.0) & (f < 1100.0)
        return rho[band]

    assert np.all(_rho(5e-4) == 0.0), "−33 dB 的弱侧应判『无定义』"
    assert np.all(_rho(2e-3) > 0.9), "−27 dB 的弱侧应保留 ρ≈+1"


# ------------------------------------------------- 局部化反相检测（打桩喂格子）
_NB = 129          # nperseg=256 的频点数
_FS_S = 100        # 让相干窗 w = max(1, round(0.5·fs/hop)) 恰为 1（逐格算术可控）


def _scan_with_cells(monkeypatch, cells, *, floor_db=None):
    """打桩 STFT，直接喂"时频格"：`cells = [(power, anti)]`。

    每格 `ZL = √(power/2)`、`ZR = ∓√(power/2)` ⇒ pL = pR = power/2、ρ = ∓1。
    **功率是精确输入**，于是能量占比、门限比例、epsilon 都能被逐位检验。
    """
    nperseg = 256
    ZL = np.zeros((_NB, 1), dtype=np.complex128)
    ZR = np.zeros((_NB, 1), dtype=np.complex128)
    for i, (power, anti) in enumerate(cells):
        amp = np.sqrt(power / 2.0)
        ZL[i, 0] = amp
        ZR[i, 0] = -amp if anti else amp
    seq = [ZL, ZR]

    def fake_stft(*_a, **_kw):
        return np.arange(_NB, dtype=float), np.zeros(1), seq.pop(0)

    monkeypatch.setattr(dsp.signal, "stft", fake_stft)
    if floor_db is not None:
        monkeypatch.setattr(dsp, "ANTI_FLOOR_DB", floor_db)
    zero = np.zeros(nperseg * 2, dtype=np.float32)
    return dsp.anti_phase_scan(zero, zero, _FS_S, nperseg)


def test_anti_phase_scan_stft_hop_is_three_quarters(monkeypatch):
    """`hop = nperseg·3//4`（4096 窗 ⇒ 跳 3072、重叠 1024）。

    这里用打桩记录 `noverlap`：跳改成 `nperseg/3//4`（重叠 3755）或
    `nperseg`（重叠 0）都会改变相干窗的**时间跨度**，从而改变"多长一段反相才算数"。
    """
    seen = []

    def fake_stft(_x, fs=None, nperseg=None, noverlap=None, window=None):
        seen.append(noverlap)
        nb = nperseg // 2 + 1
        return np.arange(nb, dtype=float), np.zeros(1), np.ones((nb, 1), dtype=np.complex128)

    monkeypatch.setattr(dsp.signal, "stft", fake_stft)
    zero = np.zeros(8192, dtype=np.float32)
    dsp.anti_phase_scan(zero, zero, FS)
    assert seen == [1024, 1024], "nperseg 4096 的 hop 必须是 3072（重叠 1024）"


def test_anti_phase_scan_needs_two_windows_of_input():
    """输入短于 `nperseg·2` 拒绝；**恰好** 2 个窗长要放行（`<` 不是 `<=`）。

    `*2` 写成 `/2` 会把 4096 采样（一个窗）也放进来——单帧算不出跨帧相干。
    """
    zero = np.zeros(8192, dtype=np.float32)
    assert dsp.anti_phase_scan(np.zeros(4096, dtype=np.float32),
                               np.zeros(4096, dtype=np.float32), FS) == {}
    assert dsp.anti_phase_scan(zero, zero, FS) != {}


def test_anti_phase_scan_scope_tiers_are_exact_energy_shares(monkeypatch):
    """分档门限 `(0.01, 0.10, 0.50)` 的**等号侧**：占比恰 1% / 10% / 50%。

    100 个等功率格（每个 2.0）里放 k 个反相格 ⇒ 占比恰为 k/100。
    等号必须落在**上一档**（1% 是"轻微"、10% 是"明显"、50% 是"严重"）；
    门限被改大（0.015/0.15/0.75）就会各自降一档。
    """
    for k, pct, scope in ((1, 1.0, "轻微"), (10, 10.0, "明显"), (50, 50.0, "严重")):
        cells = [(2.0, True)] * k + [(2.0, False)] * (100 - k)
        r = _scan_with_cells(monkeypatch, cells)
        assert r["anti_energy_pct"] == pct, "%d%% 的占比读成 %s" % (pct, r["anti_energy_pct"])
        assert r["anti_scope"] == scope, "%d%% 应判 %s，实得 %s" % (pct, scope, r["anti_scope"])


def test_anti_phase_pct_rounds_to_three_digits(monkeypatch):
    """展示口径：`anti_energy_pct` 保留 **3** 位小数。

    100 格里 1 格反相会得到恰好 1.0（看不出位数），所以用 **7 格**：1/7 = 14.2857…%
    → 14.286（4 位会变 14.2857；`100→150` 的常数变异会变 21.429）。
    """
    cells = [(2.0, True)] + [(2.0, False)] * 6
    r = _scan_with_cells(monkeypatch, cells)
    assert r["anti_energy_pct"] == 14.286
    assert r["anti_scope"] == "明显"


def test_anti_phase_rho_threshold_is_exactly_minus_0_9(monkeypatch):
    """`rho < ANTI_RHO` 的等号侧：ρ 恰为 **−0.9** 时**不算**反相。

    这里把 `mov`（时间平均）打桩成常数：分子 −0.9、分母各 1.0 ⇒
    ρ = 2·(−0.9)/(1+1) = **逐位 −0.9**。`<` 改 `<=` 会把这一格算成 100% 反相。
    """
    nperseg = 256
    ZL = np.ones((_NB, 1), dtype=np.complex128)
    ZR = np.ones((_NB, 1), dtype=np.complex128)
    seq = [ZL, ZR]

    def fake_stft(*_a, **_kw):
        return np.arange(_NB, dtype=float), np.zeros(1), seq.pop(0)

    def fake_lfilter(_b, _a, x, axis=-1):
        arr = np.asarray(x)
        return np.full(arr.shape, -0.9 + 0j if np.iscomplexobj(arr) else 1.0)

    monkeypatch.setattr(dsp.signal, "stft", fake_stft)
    monkeypatch.setattr(dsp.signal, "lfilter", fake_lfilter)
    zero = np.zeros(nperseg * 2, dtype=np.float32)
    r = dsp.anti_phase_scan(zero, zero, _FS_S, nperseg)
    assert r["anti_energy_pct"] == pytest.approx(0.0, abs=1e-12)
    assert r["anti_scope"] == "无"


def test_anti_phase_share_zero_logs_minus_300db(monkeypatch):
    """有能量但**没有**反相格：`share = 0`，`10·log10(0 + 1e-30)` = **−300.0 dB**。

    底数写成 15 ⇒ −450；epsilon 写成 0 ⇒ log10(0) = −inf（不是有限读数）。
    """
    r = _scan_with_cells(monkeypatch, [(2.0, False)] * 100)
    assert r["anti_energy_db"] == -300.0
    assert r["anti_scope"] == "无"


def test_anti_phase_floor_ratio_is_40db(monkeypatch):
    """"有能量"的门限是**本帧峰值下 40 dB**（功率比 1e-4）。

    峰值格功率 2.0，另一个反相格功率 **4e-4**（比例 2e-4，比 1e-4 深但比
    15^-3 = 2.96e-4 浅）⇒ 40 dB 口径下计入（读数 0.02%），
    底数改成 15 的变异体会把它排除（读数 0.0%）。
    """
    r = _scan_with_cells(monkeypatch, [(2.0, False), (4e-4, True)])
    assert r["anti_energy_pct"] == 0.02


def test_anti_phase_floor_equality_counts_as_energy(monkeypatch):
    """`active = power > 峰值·10^(−floor/10)` 的等号侧：**恰好等于**门限算"有能量"。

    把 `ANTI_FLOOR_DB` 取成 `10·log10(4)` ⇒ `10^(−floor/10)` 逐位等于 0.25；
    峰值格功率 2.0 ⇒ 门限恰 0.5，而反相格功率也恰是 0.5（±0.5 两声道）。
    `>` 之下它不算（读数 0），`>=` 之下算（占比 20%）。
    """
    r = _scan_with_cells(monkeypatch, [(2.0, False), (0.5, True)],
                         floor_db=10 * np.log10(4.0))
    assert r["anti_energy_pct"] == 0.0, "恰在门限上的格按 40 dB 口径不该计入"


def test_anti_phase_share_denominator_epsilon_is_load_bearing(monkeypatch):
    """`share = anti_p / (tot_p + 1e-30)`：tot_p 极小时这个 epsilon 是**有效量**。

    单格功率 1e-29、ρ = −2a²/(2a²+1e-30) ≈ −0.909（仍判反相）⇒
    占比 = 1e-29/(1e-29+1e-30) = **90.9%**（不是 100%）。
    `+1e-30` 写成 `−1e-30` ⇒ 111.1%；写成 `0.0` ⇒ 100.0。
    """
    amp = np.sqrt(5e-30)
    power = 2 * amp * amp
    r = _scan_with_cells(monkeypatch, [(power, True)])
    assert r["anti_energy_pct"] == pytest.approx(90.909, abs=0.002)
    assert r["anti_energy_pct"] == round(100.0 * power / (power + 1e-30), 3)


def test_anti_phase_frame_peak_epsilon_keeps_quiet_cells_out(monkeypatch):
    """`frame_peak = max + 1e-30`：这个 epsilon 决定**极小声的格子算不算"有能量"**。

    峰值格功率 1e-29（反相），另有 128 个功率 1.05e-33 的同相格：
    带 epsilon 的门限是 1.1e-33 ⇒ 它们**不算**（分母只剩 1e-29，占比 90.9%）；
    去掉 epsilon（或改成减）门限降到 1.0e-33 / 0.9e-33 ⇒ 它们被算进分母（占比 ~89%）。
    """
    tiny = np.sqrt(1.05e-33 / 2.0)
    peak = 2 * np.sqrt(5e-30) ** 2
    cells = [(peak, True)] + [(2 * tiny * tiny, False)] * 128
    r = _scan_with_cells(monkeypatch, cells)
    assert r["anti_energy_pct"] == pytest.approx(
        round(100.0 * peak / (peak + 1e-30), 3), abs=1e-9)


def test_anti_phase_unequal_levels_defeat_the_naive_denominator():
    """分母是 `mov(|L|²) + mov(|R|²)`（两声道功率**和**）。

    素材：L 幅度 0.5、R 幅度 0.25 且**反相**。真实相干 ρ = −2·0.125/(0.25+0.0625) = −0.8，
    不到 −0.9 ⇒ 不该报反相。把加号写成减号 ⇒ 分母只剩 0.1875 ⇒ ρ = −1.33 < −0.9，
    于是**正常的不等幅反相内容会被报成"严重"**。
    """
    n = FS
    t = np.arange(n) / FS
    left = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    right = (-0.25 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    r = dsp.anti_phase_scan(left, right, FS)
    assert r["anti_energy_pct"] == 0.0
    assert r["anti_scope"] == "无"


def test_anti_phase_silence_returns_the_early_exit_sentinel():
    """全静音 ⇒ 早退哨兵 `anti_energy_db = −120.0`、频点 `None`（不是 −300 / 0 Hz）。

    `frame_peak` 的加号写成减号会让"功率 0 > 负数"成立 ⇒ 走进正式分支、
    读数变 −300；哨兵常数被改（−120→−180）同样要能发现。
    """
    r = dsp.anti_phase_scan(np.zeros(8192, dtype=np.float32),
                            np.zeros(8192, dtype=np.float32), FS)
    assert r == {"anti_energy_db": -120.0, "anti_energy_pct": 0.0,
                 "anti_band_khz": None, "anti_scope": "无"}


# --------------------------------------------------------------- 直流
def test_dc_result_units_and_guards():
    """直流结果的口径：`sum/n`、6 位小数、`20·log10(|dc|+1e-12)`。

    * `n <= 0` 必须拒绝（`<` 会让 n=0 走到 0/0）；
    * `/n` 写成 `*n` 会把 0.125 变成 2.0；
    * 6 位写成 7 位时 0.12345675 会多出第 7 位；
    * `20*log10` 写成 `20/log10`、底数 21 都会给出别的 dB；
    * epsilon 写成 0 时 dc=0 会变成 −inf（不是有限的 −240）。
    """
    assert dsp.dc_result([0.5, -0.25], 4) == {"dc_offset": [0.125, -0.0625],
                                              "dc_max_dbfs": -18.1}
    assert dsp.dc_result([0.0], 0) == {}
    assert dsp.dc_result([0.0], 1)["dc_max_dbfs"] == -240.0
    assert dsp.dc_result([0.12345675], 1)["dc_offset"][0] == 0.123457


# --------------------------------------------------------------- 过采样
def test_oversample_uses_kaiser_beta_10():
    """4× 过采样的滤波器是 `kaiser, beta=10`（阻带约 −70 dB，规格要求 ≥ −40 dB）。

    判据取"**不是** beta=15"：两者在斜坡信号上的差约 8e-7，1e-12 的绝对容差足以分开。
    改 beta 是"会动真峰值读数"的决定（虽然很小），必须是有意识的。
    """
    x = np.sin(np.arange(64) / 6.0)
    got = dsp._oversample(x)
    ten = signal.resample_poly(x, 4, 1, axis=0, window=("kaiser", 10.0))
    fifteen = signal.resample_poly(x, 4, 1, axis=0, window=("kaiser", 15.0))
    assert np.allclose(got, ten, rtol=0, atol=1e-12)
    assert not np.allclose(got, fifteen, rtol=0, atol=1e-12)


# --------------------------------------------------------------- 真峰值扫描
def test_true_peak_scan_read_windows(monkeypatch, tmp_path):
    """分块读的**窗口**：向前借 `TP_TRIM`、向后借 `TP_TRIM`，且块长下限 65536。

    200000 帧、chunk=65536 时的前两次读必须是 `(0, 65536+TP_TRIM)` 与
    `(65536−TP_TRIM, 65664)`（`TP_TRIM = 64`）：
      * `pos − TP_TRIM` 写成 `pos + TP_TRIM` ⇒ 第二次从 65600 开始（**跳过** 64 采样）；
      * `end + TP_TRIM` 写成 `end − TP_TRIM` ⇒ 第一次只读 65472（丢掉尾部上下文）；
      * `max(1<<16, chunk)` 写成 `1<<17` ⇒ 第一次就要 131136 帧。
    """
    calls = []

    def fake_read(_path, start=0, frames=-1, dtype="float32", always_2d=True):
        calls.append((start, frames))
        return np.zeros((max(0, min(frames, 200000 - start)), 1), dtype=dtype), FS

    p = tmp_path / "long.wav"
    sf.write(str(p), np.zeros((200000, 1), dtype=np.float32), FS)
    monkeypatch.setattr(sf, "read", fake_read)
    dsp.true_peak_scan(str(p), [(0, 1000)], chunk=1 << 16)
    assert calls[0] == (0, 65536 + dsp.TP_TRIM)
    assert calls[1] == (65536 - dsp.TP_TRIM, 65536 + 2 * dsp.TP_TRIM)


def test_true_peak_scan_retry_floor_is_64k(monkeypatch, tmp_path):
    """截断/坏文件的**减半重试下限**是 65536 帧（不是 131072）。

    桩：`frames > 65600` 就抛（模拟"一次要太多帧"，65600 正是一个满块的首读）。
    chunk 默认 2^21：原实现一路减半到 65536 并成功读到第一块
    （`frames_read = 65536`、`incomplete=True`）；
    下限改成 1<<17 时连 131072 那档都过不去 ⇒ `frames_read = 0`（**前面的内容全丢**）。
    """
    def fake_read(_path, start=0, frames=-1, dtype="float32", always_2d=True):
        if frames > 65600:
            raise RuntimeError("block too large")
        return np.zeros((frames, 1), dtype=dtype), FS

    p = tmp_path / "cut.wav"
    sf.write(str(p), np.zeros((200000, 1), dtype=np.float32), FS)
    monkeypatch.setattr(sf, "read", fake_read)
    got = dsp.true_peak_scan(str(p), None)
    assert got["frames_read"] == 65536
    assert got["incomplete"] is True


def test_true_peak_scan_decibel_floor_is_1e_12(tmp_path):
    """`20·log10(pk + 1e-12)`：ε 是**加**的（−inf 会被挡住，但 −240 才是 dc=1e-12 的读数）。

    素材：4096 采样里只有一个 1e-12 的尖峰 ⇒ 真峰值 ≈ 1e-12 ⇒
    `20log10(2e-12) = −233.98`。写成 `−1e-12` ⇒ pk−1e-12 = 0 ⇒ −inf；写成 0 ⇒ −240.0。
    """
    p = tmp_path / "tiny.wav"
    x = np.zeros(4096, dtype=np.float32)
    x[100] = 1e-12
    sf.write(str(p), x, FS, subtype="FLOAT")
    got = dsp.true_peak_scan(str(p))["global"]
    assert got is not None and abs(got - (-233.98)) < 0.05, "实得 %s" % got
