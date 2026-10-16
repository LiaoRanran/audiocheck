"""响度与直流：ITU-R BS.1770-4 / EBU R128 标准测量。"""
from __future__ import annotations

from .. import dsp
from ..registry import Analyzer, register


@register
class LoudnessAnalyzer(Analyzer):
    key = "loudness"
    label = "响度（BS.1770 / R128）"
    order = 40

    def run(self, ctx) -> dict:
        if not ctx.deep:
            return {}
        # 真峰值走 **context 的共享扫描**（整轨一次过采样，`timeline` 逐段复用同一份）
        # ⇒ 全文件只做一遍 4× 过采样，而不是"整轨一遍 + 逐段最多 96 遍"。
        return dsp.loudness(ctx.path, ctx.fs, ctx.channels, ctx.frames,
                            true_peak_dbfs=ctx.true_peak_scan()["global"])


@register
class DcOffsetAnalyzer(Analyzer):
    key = "dc_offset"
    label = "直流偏移"
    order = 41

    def run(self, ctx) -> dict:
        # 复用 `basic` 那一趟解出来的逐声道有符号和 ⇒ **不再整轨解码第二遍**。
        # `basic` 在 order 10，天然先跑；拿不到（如单独 `--only dc_offset`）才自己读。
        dc = ctx.dc()
        if dc is not None and dc[1] > 0:
            return dsp.dc_result(dc[0], dc[1])
        return dsp.dc_offset(ctx.path, ctx.channels)
