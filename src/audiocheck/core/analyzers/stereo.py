"""立体声特性：声道宽度、相位一致度、单声道兼容性。"""
from __future__ import annotations

from .. import dsp
from ..registry import Analyzer, register


@register
class StereoAnalyzer(Analyzer):
    key = "stereo"
    label = "立体声"
    order = 42
    requires = ("basic",)

    def run(self, ctx) -> dict:
        if ctx.channels < 2:
            return {"corr": None, "width_db": None, "mono_compat": "单声道素材"}
        seg, _ = ctx.segment(seconds=60.0)
        out = {}
        if seg is not None and len(seg):
            out.update(dsp.stereo_features(seg[:, 0], seg[:, 1]))
        if out.get("corr") is None and ctx.result.get("_corr") is not None:
            out["corr"] = round(float(ctx.result["_corr"]), 3)
        return out
