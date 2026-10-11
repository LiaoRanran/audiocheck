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
        return dsp.loudness(ctx.path, ctx.fs, ctx.channels, ctx.frames)


@register
class DcOffsetAnalyzer(Analyzer):
    key = "dc_offset"
    label = "直流偏移"
    order = 41

    def run(self, ctx) -> dict:
        return dsp.dc_offset(ctx.path, ctx.channels)
