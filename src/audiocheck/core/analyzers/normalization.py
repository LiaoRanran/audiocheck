"""流媒体归一化目标诊断：由 LUFS 推算各平台会施加多少增益。

为什么单独列一项（而不是只报 LUFS 数值）：
LUFS 本身只是"多响"，真正影响听感的是**平台会把音量拧多少**。
被大幅衰减的母带，其响度战痕迹会等比例暴露——同一首歌在
不同平台上"劲道"不同，量化解释就在这里。

目标值为各平台公开的归一化标准（非本项目的推测）：
  * EBU R128（广播）            −23 LUFS
  * Apple Music（Sound Check）  −16 LUFS
  * Spotify / YouTube / Tidal   −14 LUFS
  * Amazon Music                −14 LUFS
注意 Netflix 的 −27 LUFS 是**对白门控**测的，与全节目 LUFS 不同口径，
故不列入——混进来会得出错误结论。
"""
from __future__ import annotations

from ..registry import Analyzer, register

#: (平台, 目标 LUFS, 来源)
TARGETS = [
    ("EBU R128 广播", -23.0, "EBU R128 / Tech 3343"),
    ("Apple Music", -16.0, "Apple Sound Check"),
    ("Spotify", -14.0, "Spotify 母带级归一化"),
    ("YouTube", -14.0, "YouTube 响度归一化"),
    ("Amazon Music", -14.0, "Amazon Music 归一化"),
    ("Tidal", -14.0, "Tidal 母带级归一化"),
]

#: 需要衰减超过这个量就值得提示（平台会把你母带的响度战放大）
ATTEN_WARN_DB = 3.0


@register
class NormalizationAnalyzer(Analyzer):
    key = "normalization"
    label = "流媒体归一化"
    order = 45
    requires = ("loudness",)

    def run(self, ctx) -> dict:
        lufs = ctx.result.get("lufs")
        if lufs is None:
            return {}
        rows = []
        for name, target, src in TARGETS:
            gain = round(target - lufs, 1)
            rows.append({"platform": name, "target_lufs": target,
                         "gain_db": gain, "src": src})
        # 需要衰减最多的平台（gain 最负）
        worst = min(rows, key=lambda r: r["gain_db"])
        out = {
            "norm_targets": rows,
            "norm_max_atten_db": abs(min(0.0, worst["gain_db"])),
            "norm_over_loud": bool(worst["gain_db"] < -ATTEN_WARN_DB),
        }
        if out["norm_over_loud"]:
            out["norm_note"] = (
                "%s 需要把它压低 %.1f dB 才达标 —— 母带比目标响这么多，"
                "平台一拧，响度战留下的压缩痕迹会被等比例放大。"
                % (worst["platform"], abs(worst["gain_db"])))
        else:
            out["norm_note"] = "各平台只需很小调整即可归一化。"
        return out
