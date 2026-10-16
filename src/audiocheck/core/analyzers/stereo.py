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
            # B14 自研：**局部化反相检测**。全局 corr 是"整段一个数"，
            # 只要有一半时间是正常立体声它就可能是正的——而"某段人声被反相加倍"
            # "某频段被极性反转"这类缺陷会被平均值吃掉。这里按**频率 × 时间**
            # 分开看相位，报出反相格占比/能量占比/最集中的频段。
            # 分档门限**显式传** `ctx.thresholds`（C0-3 口径：不显式传，用户在
            # 设置里改它们就"设了没反应"）。
            th = ctx.thresholds
            try:
                out.update(dsp.anti_phase_scan(
                    seg[:, 0], seg[:, 1], ctx.fs,
                    scope_bands=(th.anti_scope_minor, th.anti_scope_major,
                                 th.anti_scope_severe)))
            except Exception as e:                             # noqa: BLE001
                # 相位扫描失败不该拖垮立体声分析（其余字段仍有效），
                # 但**不许静默**——写进 errors，CLI/JSON 的告警与退出码才看得见。
                ctx.result["errors"].append("stereo/anti_phase: %s" % str(e)[:120])
        if out.get("corr") is None and ctx.result.get("_corr") is not None:
            out["corr"] = round(float(ctx.result["_corr"]), 3)
        # 声明显示元信息（GUI 指标卡按这个渲染；不声明就走默认）
        ctx.metric("anti_energy_pct", "反相能量占比", unit="%", digits=3, group="立体声")
        # 与上一行是**同一个量的两种写法**（% 给人看、dB 给算）：标签必须区分，
        # 否则界面上两个"反相能量占比"并排，用户没法知道该看哪个。
        ctx.metric("anti_energy_db", "反相能量（对数）", unit=" dB", digits=1, group="立体声")
        ctx.metric("anti_band_khz", "反相集中频段", unit="kHz", digits=2, group="立体声")
        ctx.metric("anti_scope", "反相范围", group="立体声")
        return out
