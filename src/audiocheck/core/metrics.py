"""指标元信息 —— 决定一个数字在界面/报告里长什么样。

分析器通过 `ctx.metric(key, ...)` 声明，GUI 与导出器据此自动渲染，
因此新增指标不需要改任何界面代码。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    unit: str = ""
    digits: int = 1
    signed: bool = False
    group: str = "其它"          # 分组，用于「全部指标」表格分节
    primary: bool = False        # 是否进入总览的指标卡

    def fmt(self, value) -> str:
        if value is None:
            return "—"
        if isinstance(value, bool):
            return "是" if value else "否"
        if isinstance(value, (int, float)):
            spec = "%+.*f" if self.signed else "%.*f"
            return (spec % (self.digits, float(value))) + self.unit
        return "%s%s" % (value, self.unit)


#: 引擎内置指标的元信息（分析器可覆盖或追加）
BUILTIN: dict[str, Metric] = {
    m.key: m for m in [
        Metric("sr", "采样率", " Hz", 0, group="容器", primary=True),
        Metric("bits", "位深", group="容器"),
        Metric("format", "容器格式", group="容器"),
        Metric("channels", "声道数", group="容器"),
        Metric("seconds", "时长", " s", 1, group="容器"),
        Metric("size_mb", "体积", " MB", 1, group="容器"),
        Metric("kbps", "估算码率", " kbps", 0, group="容器", primary=True),
        Metric("peak_dbfs", "峰值", " dBFS", 2, group="电平", primary=True),
        Metric("rms_dbfs", "RMS", " dBFS", 2, group="电平", primary=True),
        Metric("crest_db", "波峰因数", " dB", 1, group="电平"),
        Metric("dr_db", "动态范围 DR", " dB", 1, group="电平", primary=True),
        Metric("clip_pct", "削波样本", " %", 4, group="电平", primary=True),
        Metric("dc_max_dbfs", "直流偏移", " dBFS", 1, group="电平"),
        Metric("lufs", "集成响度", " LUFS", 1, group="响度", primary=True),
        Metric("lra", "响度范围 LRA", " LU", 1, group="响度", primary=True),
        Metric("true_peak_dbfs", "真峰值", " dBTP", 2, signed=True, group="响度", primary=True),
        Metric("plr", "Peak-to-Loudness", " dB", 1, group="响度", primary=True),
        Metric("st_max", "短时最大响度", " LUFS", 1, group="响度"),
        Metric("st_min", "短时最小响度", " LUFS", 1, group="响度"),
        Metric("cutoff_hz", "真实截止", " Hz", 1, group="带宽", primary=True),
        Metric("cutoff_ratio", "占奈奎斯特", "", 3, group="带宽"),
        Metric("brickwall_db_per_oct", "砖墙陡度", " dB/oct", 1, group="带宽"),
        Metric("centroid_hz", "频谱重心", " Hz", 1, group="频谱"),
        Metric("rolloff85_hz", "85% 滚降", " Hz", 1, group="频谱"),
        Metric("rolloff95_hz", "95% 滚降", " Hz", 1, group="频谱"),
        Metric("flatness", "谱平坦度", "", 5, group="频谱"),
        # C0-5 正名：这个数**不是**噪声底，它是**频谱的第 5 百分位**（`dsp.py`）。
        # 叫"噪声底"会让用户以为它来自静音段的测量——那需要一个"静音段"判据，
        # 属 C1 的新阈值，本轮不做。先按实际含义命名。
        # ⚠ 数据键仍叫 `noise_floor_db`（JSON 兼容），但**界面标签不许再写"噪声底"**。
        Metric("noise_floor_db", "谱低端百分位", " dB", 1, group="频谱"),
        Metric("corr", "相位相关", "", 3, group="立体声", primary=True),
        Metric("width_db", "声道宽度", " dB", 1, group="立体声", primary=True),
        Metric("mono_compat", "单声道兼容", group="立体声"),
        Metric("lr_level_diff_db", "L/R 电平差", " dB", 2, group="立体声"),
        Metric("vendor", "编码器串", group="元数据", primary=True),
    ]
}
