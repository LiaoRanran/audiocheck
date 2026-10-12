"""时频视图的数据生产者（频谱图 / 瀑布图）。

与"分析器"分开：这些不是判定用的指标，而是给人看的图。
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


def spectrogram(path: str, seg_seconds: float = 12.0, nperseg: int = 4096,
                hop: int = 3072, max_cols: int = 900):
    """中段 STFT，返回 (t, f, S_db)。列数过多时按峰值抽样以保证绘图速度。"""
    info = sf.info(path)
    fs, total = info.samplerate, info.frames
    start = int(total * 0.3)
    n = int(min(seg_seconds * fs, max(0, total - start)))
    if n < nperseg * 4:
        start, n = 0, min(total, max(nperseg * 8, int(seg_seconds * fs)))
    x, _ = sf.read(path, start=start, frames=n, dtype="float32", always_2d=True)
    m = x.mean(axis=1)
    f, t, S = signal.spectrogram(m, fs=fs, nperseg=nperseg,
                                 noverlap=max(0, nperseg - hop), window="hann")
    if S.shape[1] > max_cols:
        idx = np.linspace(0, S.shape[1] - 1, max_cols).astype(int)
        S, t = S[:, idx], t[idx]
    return t, f, _to_db(S)


def waterfall(path: str, n_slices: int = 44, seg_seconds: float = 25.0, nperseg: int = 4096):
    """3D 瀑布图数据：沿时间取 n_slices 条谱线。"""
    info = sf.info(path)
    fs, total = info.samplerate, info.frames
    start = int(total * 0.15)
    n = int(min(seg_seconds * fs, max(0, total - start)))
    if n < nperseg * 4:
        start, n = 0, min(total, max(nperseg * 8, int(seg_seconds * fs)))
    x, _ = sf.read(path, start=start, frames=n, dtype="float32", always_2d=True)
    m = x.mean(axis=1)
    hop = max(1, n // n_slices)
    f, t, S = signal.spectrogram(m, fs=fs, nperseg=nperseg,
                                 noverlap=max(0, nperseg - hop), window="hann")
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
