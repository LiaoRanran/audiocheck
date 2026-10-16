"""时频视图的数据生产者（频谱图 / 瀑布图）。

与"分析器"分开：这些不是判定用的指标，而是给人看的图。

## B14 建模精度三修（三种图做到极致）

1. **功率合并，不是单声道均值**。旧版 `x.mean(axis=1)` 把立体声压成单声道——
   而 L/R **反相**的内容（真实存在的录音缺陷）在均值里**直接抵消、还不报错**：
   这是"测量版的静默降级"，图上看起来"很干净"，实际把要查的东西抹掉了。
   改成**逐声道功率谱后按功率平均**（功率非负，无抵消）。
2. **全曲覆盖，不是中段 12 秒**。旧版只取 30% 处的 12s——撞"代表性"：
   前后段的缺陷根本进不了图。改为**默认整曲**（`max_seconds` 可设上限兜底）。
3. **峰值保持抽样，不是等距抽样**。旧版列数超限时 `linspace` 取点，
   会**漏掉瞬态**（被抽掉的恰好是尖峰）。改成**峰值保持**：每格取该格内最大功率。
"""
from __future__ import annotations

import numpy as np
import soundfile as sf
from scipy import signal

from . import dsp


def _to_db(S: np.ndarray) -> np.ndarray:
    """功率谱 → dB，并降到 float32。

    内存不是小事：S 是 (nperseg/2+1) × N 的矩阵，界面层会同时缓存十几份
    （缓存见 MainWindow._cache）。float64 → float32 直接把缓存和 pcolormesh
    内部缓冲减半，而 dB 值只需要 0.01 dB 量级的精度——float32 有 7 位有效数字，
    余量绰绰有余（-90.00 dB 的表示误差约 1e-5 dB）。
    """
    return (10 * np.log10(S + 1e-20)).astype(np.float32)


def _stft_power(x2d: np.ndarray, fs: int, nperseg: int, hop: int):
    """逐声道功率谱 → 按**功率**合并，返回 (f, t, S_power)。

    ⚠ 合并必须在**功率域**（`S` 非负）：均值域会抵消反相内容（见模块 docstring §1）。
    ⚠ 合并用**求和**（总功率），与 `core/context.py::spectrum` 同一约定——
    旧版这里是 `np.mean(powers)`（按声道数平均），两条路径口径不一致：
    1 声道文件与"第 2 声道静音"的 2 声道文件会差 3.01 dB。
    求和后两者电平相同（静音声道功率为 0），且物理上也对：两只音箱 = 2 份能量。
    对显式峰值归一化的频谱图（`S - nanmax(S)`）**恒等**——全局缩放被约掉。
    """
    f = t = None
    tot = None
    for ch in range(x2d.shape[1]):
        f, t, S = signal.spectrogram(x2d[:, ch], fs=fs, nperseg=nperseg,
                                     noverlap=max(0, nperseg - hop), window="hann")
        tot = S if tot is None else tot + S
    return f, t, tot


def _peak_hold(S: np.ndarray, t: np.ndarray, max_cols: int):
    """列数超限时按**峰值保持**抽稀（每格取最大功率，瞬态不丢）。"""
    ncols = S.shape[1]
    if ncols <= max_cols:
        return S, t
    edges = np.linspace(0, ncols, max_cols + 1).astype(int)
    out = np.empty((S.shape[0], max_cols), dtype=S.dtype)
    tt = np.empty(max_cols, dtype=t.dtype)
    for i in range(max_cols):
        lo = edges[i]
        hi = max(lo + 1, edges[i + 1])
        out[:, i] = S[:, lo:hi].max(axis=1)
        tt[i] = t[lo:hi].mean()
    return out, tt


def _read(path: str, fs: int, total: int, nperseg: int, max_seconds: float | None):
    """读整曲（或 max_seconds 上限）的立体声；过短则整段。"""
    n = total if max_seconds is None else min(total, int(max_seconds * fs))
    if n < nperseg * 4:
        n = total
    x, _ = sf.read(path, start=0, frames=n, dtype="float32", always_2d=True)
    return x


#: **视图** STFT 的可选窗长（`nperseg`）。B14：补强"不可调"这条（竞品 Spek 提供
#: 1024/2048/4096/8192 四档）。只列三档，理由写清楚：
#:   2048 → Δf 21.5 Hz  时间更细（瞬态/咔哒声的毛刺看得见）
#:   4096 → Δf 10.77 Hz **默认**：经典权衡点（时间/频率各让一半）
#:   8192 → Δf 5.38 Hz  频率更细（砖墙边缘、陷波的边缘更锐）
#:
#: ⚠ **这里只管出图**。`context.spectrum()`（喂判定的曲线，16384-FFT）**不给调**：
#: 改了它，截止频率/频谱倾斜/假 Hi-Res 的结论就跟着变，而结论必须可复算。
#: 一个是"看多少"，一个是"凭什么判"——后者必须锁死。
VIEW_NPERSEG_CHOICES = (2048, 4096, 8192)

#: 默认窗长 = 经典权衡点。
DEFAULT_VIEW_NPERSEG = 4096

#: **双分辨率 STFT（C7，2026-10-16 实现）**：根治"低频糊"。
#: 默认 4096 窗在 <546Hz 是"行距细过 bin"（Δf 10.77Hz 比显示行距粗），
#: 低频细节被插值抹平（E1 实测 50Hz 占行 360/720 不变——加行数治不了）。
#: 16384 窗 Δf 2.69Hz，可分辨间距 30→10Hz（3×）。**只动视图 STFT**：
#: 判定曲线走 `context.spectrum()` 的 16384 锁死口径，与这里两套独立参数
#: （早已解耦），改视图窗不会动结论（不撞"结论可复算"宪法）。
DUAL_RES_NPERSEG = 16384
#: 双分辨率只覆盖"主窗在该频段比显示行距粗"的低频带。546Hz = 4096 窗的
#: 显示插值边界（docs/views_coverage.md）；高于它主窗已足够细，不必多算。
DUAL_RES_CUTOFF_HZ = 546.0


def _hop_for(nperseg: int) -> int:
    """75% 重叠（`hop = 3/4 · nperseg`）——时频分析的惯例重叠量。

    **阴影随 nperseg 派生，不独立设**：否则调大窗长时重叠比例会悄悄变小
    （比如仍用 3072 配 8192），时间平滑度跟着变，而用户以为自己只改了一件事。
    """
    return max(1, int(nperseg * 3 // 4))


def _align_time(S: np.ndarray, t_src: np.ndarray, t_dst: np.ndarray) -> np.ndarray:
    """把 (n_f, n_t_src) 矩阵沿时间轴**最近邻**重采样到 (n_f, n_t_dst)。

    双分辨率两段 STFT 的 hop 不同 ⇒ 时间帧数不同（16384 窗帧少、4096 窗帧多）。
    两者覆盖同一整曲，最近邻映射即可：本项目的纪律是"**不制造假插值分辨率**"，
    最近邻是诚实选择；且时间对齐精度远粗于显示列宽（200ms），不影响判读。
    """
    t_src = np.asarray(t_src, dtype=float)
    t_dst = np.asarray(t_dst, dtype=float)
    idx = np.clip(np.searchsorted(t_src, t_dst), 0, len(t_src) - 1)
    return S[:, idx]


def _dual_res_spectrogram(x2d, fs, nperseg, hop, max_cols, cutoff_hz):
    """双分辨率合并：低频段（< cutoff）用 `DUAL_RES_NPERSEG` 窗重算，拼成
    「低频细、高频粗」的**非均匀**频率轴，返回 `(f, t, S_power)`。

    ## 为什么返回非均匀轴也对

    下游 `charts._resample_rows` 用 `searchsorted(f, …)` 把**任意单调 f** 重采样
    到对数显示网格——低频细轴自然铺满更多显示行，高频粗轴占少行。**真实低频
    细节**（可分辨间距 3×）因此进图，而不是被插值抹平。

    ## 代价

    低频段多跑一遍 16384 窗 ≈ 一次额外 pass。实测同重叠比下 16384 窗本身
    ≈1× 主窗耗时，所以总视图成本 ≈2×（可接受：视图算一次即缓存）。
    """
    # 主分辨率（用户选的窗）：低频段也先有一份粗值打底
    f_hi, t_hi, S_hi = _stft_power(x2d, fs, nperseg, hop or _hop_for(nperseg))
    # 低频超分辨率：固定 16384 窗（Δf 2.69Hz）
    f_lo, t_lo, S_lo = _stft_power(x2d, fs, DUAL_RES_NPERSEG, _hop_for(DUAL_RES_NPERSEG))

    keep_lo = f_lo < cutoff_hz
    keep_hi = f_hi >= cutoff_hz
    # 保底：cutoff 越界不应发生；退化为单分辨率（仍是正确结果，不静默降级）
    if not keep_lo.any() or not keep_hi.any():
        return f_hi, t_hi, S_hi

    # 细轴时间对齐到粗轴帧（最近邻），再按频率拼成单调非均匀轴
    S_lo_kept = _align_time(S_lo[keep_lo], t_lo, t_hi)
    f = np.concatenate([f_lo[keep_lo], f_hi[keep_hi]])
    S = np.concatenate([S_lo_kept, S_hi[keep_hi]], axis=0)
    # 列数峰值保持抽稀（与主流程同一约定）
    S, t_hi = _peak_hold(S, t_hi, max_cols)
    return f, t_hi, S


def spectrogram(path: str, nperseg: int = DEFAULT_VIEW_NPERSEG, hop: int | None = None,
                max_cols: int = 900, max_seconds: float | None = None,
                dual_resolution: bool = True,
                dual_cutoff_hz: float = DUAL_RES_CUTOFF_HZ):
    """整曲 STFT，返回 (t, f, S_db)。列数超限时**峰值保持**抽稀。

    `nperseg` 见 `VIEW_NPERSEG_CHOICES`（只影响出图，不影响判定）。

    `dual_resolution=True`（默认）：低频段（< `dual_cutoff_hz`）用 16384 窗
    重算并并入，根治"低频糊"（见 `DUAL_RES_*` 常量与 `_dual_res_spectrogram`）。
    **只动视图 STFT**：判定曲线（16384 锁死）走 `context.spectrum()`，与本处
    两套独立参数、互不影响。
    """
    info = sf.info(path)
    fs, total = info.samplerate, info.frames
    x = _read(path, fs, total, nperseg, max_seconds)
    if dual_resolution and int(nperseg) < DUAL_RES_NPERSEG:
        f, t, S = _dual_res_spectrogram(x, fs, nperseg, hop, max_cols, dual_cutoff_hz)
    else:
        f, t, S = _stft_power(x, fs, nperseg, hop or _hop_for(nperseg))
        S, t = _peak_hold(S, t, max_cols)
    return t, f, _to_db(S)


def waterfall(path: str, n_slices: int = 44, nperseg: int = DEFAULT_VIEW_NPERSEG,
              max_seconds: float | None = None):
    """3D 瀑布图数据：沿时间取 n_slices 条谱线（整曲覆盖）。

    窗长**沿时间铺开**：`hop = len / n_slices`，让 n_slices 片覆盖整曲
    （而不是头 n_slices 个 hop）。`nperseg` 见 `VIEW_NPERSEG_CHOICES`。
    """
    info = sf.info(path)
    fs, total = info.samplerate, info.frames
    x = _read(path, fs, total, nperseg, max_seconds)
    hop = max(1, x.shape[0] // n_slices)
    f, t, S = _stft_power(x, fs, nperseg, hop)
    if S.shape[1] > n_slices:
        idx = np.linspace(0, S.shape[1] - 1, n_slices).astype(int)
        S, t = S[:, idx], t[idx]
    return t, f, _to_db(S)


def _peak_hold_abs(rho: np.ndarray, t: np.ndarray, max_cols: int):
    """列数超限时按**最极端值**抽稀（对相关系数场而言，"峰值保持"= 取 |ρ| 最大者）。

    ⚠ 不能直接用 `_peak_hold`：那个取 `max`，而 ρ 有正负——取 max 会让整张图
    偏向 +1（"看着都是同相"），把要查的反相区悄悄抹平。对**有符号**的场，
    "最极端"才是正确的保持量。
    """
    ncols = rho.shape[1]
    if ncols <= max_cols:
        return rho, t
    edges = np.linspace(0, ncols, max_cols + 1).astype(int)
    out = np.empty((rho.shape[0], max_cols), dtype=rho.dtype)
    tt = np.empty(max_cols, dtype=t.dtype)
    for i in range(max_cols):
        lo = edges[i]
        hi = max(lo + 1, edges[i + 1])
        blk = rho[:, lo:hi]
        out[:, i] = blk[np.arange(blk.shape[0]), np.argmax(np.abs(blk), axis=1)]
        tt[i] = t[lo:hi].mean()
    return out, tt


def stereo_field(path: str, nperseg: int = DEFAULT_VIEW_NPERSEG, hop: int | None = None,
                 max_cols: int = 900, max_seconds: float | None = None):
    """**立体声相位场**：L/R 在每个时频格的相关系数 ρ ∈ [-1, 1]。

    ## 为什么这是本项目该有、而常规工具没有的一张图

    L/R 的**相位关系**是录音缺陷的独立一类（反相接线、单声道化造假、
    假立体声拓宽），而它**在频谱图上完全看不见**——频谱图丢掉相位，
    常规做法更是先把立体声压成单声道（`L+R`），反相内容直接抵消。
    本项目已经把"不抵消"修好了（见模块 docstring §1），这张图是**把它画出来**：
    ρ = +1 同相（正常单声道内容）/ ρ ≈ 0 不相关（真立体声）/ ρ = −1 **完全反相**。

    ## 数学

    ρ = Re(L·conj(R)) / (|L|·|R|)，**实现在 `dsp.phase_field` 一处**——
    判定层的 `dsp.anti_phase_scan` 用的是同一个函数（同一个数学不许有两份代码）。

    单声道文件返回 `(None, None, None)`：**没有"立体声相位"这回事**，
    不许拿单声道的 ρ≡+1 冒充"完美的同相立体声"。
    """
    info = sf.info(path)
    fs, total = info.samplerate, info.frames
    x = _read(path, fs, total, nperseg, max_seconds)
    if x.shape[1] < 2:
        return None, None, None
    hop = hop or _hop_for(nperseg)
    f, t, rho = dsp.phase_field(x, fs, nperseg=nperseg, hop=hop)
    rho, t = _peak_hold_abs(rho, t, max_cols)
    return t, f, rho


def compact_curve(f, db, n: int = 1500):
    """把频谱抽稀到 n 点（log 等距），便于列表长期持有而不吃内存。"""
    if f is None or db is None:
        return None, None
    idx = np.unique(np.geomspace(1, len(f) - 1, min(n, len(f) - 1)).astype(int))
    idx = idx[idx > 0]
    return f[idx], db[idx]
