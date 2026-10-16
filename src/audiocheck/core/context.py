"""分析上下文 —— 分析器之间共享数据的唯一通道。

两个作用：
1. **共享昂贵计算**（频谱、解码片段只算一次，多个分析器复用）；
2. **承载累积结果**（`ctx.result` 边跑边写，后置分析器可读取前置结论）。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np
import soundfile as sf
from scipy import signal

from ..config import Thresholds
from .metrics import BUILTIN, Metric


def io_path(p: str) -> str:
    """文件系统 I/O 用的路径：Windows 上补 `\\\\?\\` 长路径前缀（绕开 MAX_PATH 260）。

    ⚠ `\\\\?\\` 会**关闭**所有路径规范化：必须吃**完全限定的绝对路径**，
    `.`/`..`/正斜杠都不再解释 ⇒ 先 `os.path.abspath`。
    ⚠ UNC（`\\\\server\\share`）必须写成 `\\\\?\\UNC\\server\\share`。
    ⚠ 实测（2026-10-17）：`\\\\?\\` 被 **libsndfile（sf.read/info/write）**、
    内建 `open`、`mutagen.File`、`os.walk/isfile/getsize` 全部接受；普通长路径则
    在 libsndfile 处直接 `System error`。非 Windows 原样返回（零影响）。
    """
    if os.name != "nt":
        return p
    ap = os.path.abspath(p)
    if ap.startswith("\\\\?\\"):
        return ap
    if ap.startswith("\\\\"):                    # UNC
        return "\\\\?\\UNC\\" + ap[2:]
    return "\\\\?\\" + ap


def display_path(p: str) -> str:
    """去掉 `\\\\?\\` 前缀，还原成给人看 / 进 JSON 的普通路径。"""
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[8:]
    if p.startswith("\\\\?\\"):
        return p[4:]
    return p


@dataclass
class AnalysisContext:
    path: str                       # **文件系统 I/O 用**（Windows 上可能带 \\?\ 前缀）
    thresholds: Thresholds = field(default_factory=Thresholds)
    deep: bool = True
    result: dict = field(default_factory=dict)
    metrics: dict[str, Metric] = field(default_factory=dict)
    #: 用户给的**原始路径**（显示 / 进 JSON 用）。None ⇒ 与 `path` 相同。
    src_path: str | None = None
    _info: object = None
    _spec: tuple | None = None
    _seg: tuple | None = None
    _tp_scan: dict | None = None
    _dc: tuple | None = None

    @property
    def shown_path(self) -> str:
        """对外展示 / 落 JSON 的路径（剥掉 `\\\\?\\`）。"""
        return display_path(self.src_path if self.src_path is not None else self.path)

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

    def true_peak_scan(self) -> dict:
        """**整轨与逐段共用的真峰值扫描**（只做一次 4× 过采样）。

        为什么放在 context（而不是让 `timeline` 自己扫）：`loudness` 要整轨一个数、
        `timeline` 要逐段一个数，两处各扫一遍等于把 4× 过采样做两次
        （实测 12 分钟文件上 `timeline` 占整条分析 44%，真峰值是主项）。
        段区间由 `timeline.segment_spans` **算一次**（与它自己用的完全是同一份，
        不许两处各推一套），懒计算 + 缓存 ⇒ 谁先要谁触发，后来者零成本。

        返回 `dsp.true_peak_scan` 的字典；`spans` 与 `timeline` 的行**一一对应**。
        """
        if self._tp_scan is None:
            from . import dsp
            from .analyzers.timeline import segment_spans  # 懒导入，避免环
            spans, _capped, _hop, _seg = segment_spans(self.frames, self.fs, self.thresholds)
            self._tp_scan = dsp.true_peak_scan(self.path, spans)
        return self._tp_scan

    def set_dc(self, sums, frames: int) -> None:
        """登记**逐声道有符号和**与帧数（`basic` 的那一趟顺手算出来的）。

        直流偏移本来是**独立的一遍整轨解码**（`dsp.dc_offset`）。而 `basic` 已经在
        同一份样本上跑过一趟，多累加一个 `b.sum(axis=0)` 几乎免费 ⇒ 让 `dc_offset`
        分析器直接取这里的值，**省掉一遍整文件解码**（实测 12 分钟文件上约 2 s）。
        `basic` 在 order 10，`dc_offset` 在 41 ⇒ 顺序天然满足。
        """
        self._dc = (sums, frames)

    def dc(self):
        return self._dc

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
