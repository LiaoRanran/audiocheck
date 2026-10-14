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
    """
    f = t = None
    powers = []
    for ch in range(x2d.shape[1]):
        f, t, S = signal.spectrogram(x2d[:, ch], fs=fs, nperseg=nperseg,
                                     noverlap=max(0, nperseg - hop), window="hann")
        powers.append(S)
    return f, t, np.mean(powers, axis=0)


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


def spectrogram(path: str, nperseg: int = 4096, hop: int = 3072, max_cols: int = 900,
                max_seconds: float | None = None):
    """整曲 STFT，返回 (t, f, S_db)。列数超限时**峰值保持**抽稀。"""
    info = sf.info(path)
    fs, total = info.samplerate, info.frames
    x = _read(path, fs, total, nperseg, max_seconds)
    f, t, S = _stft_power(x, fs, nperseg, hop)
    S, t = _peak_hold(S, t, max_cols)
    return t, f, _to_db(S)


def waterfall(path: str, n_slices: int = 44, nperseg: int = 4096,
              max_seconds: float | None = None):
    """3D 瀑布图数据：沿时间取 n_slices 条谱线（整曲覆盖）。"""
    info = sf.info(path)
    fs, total = info.samplerate, info.frames
    x = _read(path, fs, total, nperseg, max_seconds)
    hop = max(1, x.shape[0] // n_slices)
    f, t, S = _stft_power(x, fs, nperseg, hop)
    if S.shape[1] > n_slices:
        idx = np.linspace(0, S.shape[1] - 1, n_slices).astype(int)
        S, t = S[:, idx], t[idx]
    return t, f, _to_db(S)


def compact_curve(f, db, n: int = 1500):
    """把频谱抽稀到 n 点（log 等距），便于列表长期持有而不吃内存。"""
    if f is None or db is None:
        return None, None
    idx = np.unique(np.geomspace(1, len(f) - 1, min(n, len(f) - 1)).astype(int))
    idx = idx[idx > 0]
    return f[idx], db[idx]
