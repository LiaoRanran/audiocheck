"""分析上下文 —— 分析器之间共享数据的唯一通道。

两个作用：
1. **共享昂贵计算**（频谱、解码片段只算一次，多个分析器复用）；
2. **承载累积结果**（`ctx.result` 边跑边写，后置分析器可读取前置结论）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import soundfile as sf
from scipy import signal

from ..config import Thresholds
from .metrics import BUILTIN, Metric


@dataclass
class AnalysisContext:
    path: str
    thresholds: Thresholds = field(default_factory=Thresholds)
    deep: bool = True
    result: dict = field(default_factory=dict)
    metrics: dict[str, Metric] = field(default_factory=dict)
    _info: object = None
    _spec: tuple | None = None
    _seg: tuple | None = None

    # ---------------- 基础信息 ----------------
    @property
    def info(self):
        if self._info is None:
            self._info = sf.info(self.path)
        return self._info

    @property
    def fs(self) -> int:
        return self.info.samplerate

    @property
    def channels(self) -> int:
        return self.info.channels

    @property
    def frames(self) -> int:
        return self.info.frames

    @property
    def seconds(self) -> float:
        return self.frames / self.fs if self.fs else 0.0

    # ---------------- 共享计算 ----------------
    def spectrum(self, seg_seconds: float = 20.0, nperseg: int = 16384):
        """三分位取样做 Welch 平均，返回 (freq, dB)。结果缓存。

        B14 精度：**逐声道功率合并，不是单声道均值**。`x.mean(axis=1)` 会把
        L/R **反相**的内容抵消掉——而那是真实存在的录音缺陷，且这条谱是喂给
        **判定**的（截止频率、频谱倾斜、假 Hi-Res 盲检测都读它）。同型 bug 在
        `views.py`（频谱图/瀑布图）已修，但那条只管出图；**这条管结论，更要紧**。
        """
        if self._spec is not None:
            return self._spec
        acc = None
        f = None
        for frac in (0.25, 0.5, 0.75):
            start = int(self.frames * frac)
            n = int(min(seg_seconds * self.fs, self.frames - start))
            if n < nperseg * 2:
                continue
            x, _ = sf.read(self.path, start=start, frames=n, dtype="float32", always_2d=True)
            tot = None
            for ch in range(x.shape[1]):
                c = x[:, ch] - x[:, ch].mean()   # 去直流（在功率合并之前，逐声道）
                if not np.any(np.abs(c) > 1e-7):
                    continue                     # 该声道纯静音：功率为 0，不参与合并
                f, p = signal.welch(c, fs=self.fs, nperseg=nperseg,
                                    noverlap=nperseg // 2, window="hann")
                tot = p if tot is None else tot + p
            if tot is None:
                continue
            acc = tot if acc is None else acc + tot
        if acc is None:
            self._spec = (None, None)
        else:
            self._spec = (f, 10 * np.log10(acc + 1e-20))
        return self._spec

    def segment(self, seconds: float = 60.0, frac: float = 0.3):
        """取一段解码样本（供立体声等需要时域数组的分析器复用）。"""
        if self._seg is None:
            start = int(self.frames * frac)
            n = int(min(seconds * self.fs, max(0, self.frames - start - 1)))
            if n <= 0:
                self._seg = (None, self.fs)
            else:
                x, _ = sf.read(self.path, start=start, frames=n,
                               dtype="float32", always_2d=True)
                self._seg = (x, self.fs)
        return self._seg

    # ---------------- 指标声明 ----------------
    def metric(self, key: str, label: str, **kw) -> None:
        """登记/覆盖一个指标的显示元信息。"""
        if key in BUILTIN and not kw:
            self.metrics.setdefault(key, BUILTIN[key])
            return
        base = BUILTIN.get(key)
        if base is not None:
            merged = {**base.__dict__, **kw}
            self.metrics[key] = Metric(**merged)
        else:
            self.metrics[key] = Metric(key=key, label=label, **kw)

    def seed_metrics(self) -> None:
        for k, m in BUILTIN.items():
            self.metrics.setdefault(k, m)


def to_native(obj):
    """把 numpy 标量递归转成原生类型（json 友好）。

    `np.float32` **不是** `float` 的子类，直接 dump 会 TypeError；
    `np.float64` 反而是——所以这类 bug 只会零星出现，更难查。
    """
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, dict):
        return {k: to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_native(v) for v in obj]
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    return obj
