"""判定与建议 —— 必须最后跑，因为它读取前面所有分析器的结论。

判定哲学：**同一个截止点在不同容器里含义完全不同**。
44.1k 容器的 19.6kHz 是"轻微带限"，48k 容器的 21.4kHz 却是"疑似升频"。
"""
from __future__ import annotations

from ...config import LEVEL_CN
from ..registry import Analyzer, register


def bandwidth_verdict(fs: int, cutoff_hz: float | None, th) -> tuple[str, str]:
    """只依据容器采样率 + 真实截止点给出带宽判定。"""
    if cutoff_hz is None:
        return "无法判定（无有效内容）", "warn"
    k = cutoff_hz / 1000.0
    nyq = fs / 2

    if fs <= 45000:
        if k >= th.cd_full:
            return "CD 级满带宽", "ok"
        if k >= th.ltd:
            return "轻微带限（≈320kbps 有损，或母带本身如此）", "warn"
        if k >= th.lossy:
            return "有损溯源（≈192–256kbps 转码）", "bad"
        return "强有损溯源（≤128kbps）", "bad"

    if fs <= 50000:
        if k >= th.full_48:
            return "真 48kHz 带宽", "ok"
        if k >= th.up_44:
            return "疑似 44.1kHz 升频（内容止于 %.1fkHz）" % k, "bad"
        if k >= th.ltd:
            return "带宽偏窄（%.1fkHz）——48k 容器里有损/带限" % k, "warn"
        if k >= th.lossy:
            return "有损溯源（≈192–256kbps 转码）", "bad"
        return "强有损溯源（≤128kbps）", "bad"

    if k <= th.hi_ok:
        return "伪 Hi-Res（容器 %.1fkHz，内容仅到 %.1fkHz）" % (fs / 1000, k), "bad"
    if cutoff_hz / nyq >= th.hi_ratio:
        return "真 Hi-Res（内容到 %.1fkHz，占奈奎斯特 %.0f%%）" % (k, cutoff_hz / nyq * 100), "ok"
    return "半 Hi-Res（内容 %.1fkHz / 容器 %.1fkHz）" % (k, fs / 1000), "warn"


def quality_tags(r: dict, th) -> list[str]:
    """在带宽结论后追加其它维度的标记（不改变带宽等级）。"""
    tags = []
    slope = r.get("brickwall_db_per_oct")
    # slope 是「往上一个倍频程掉了多少 dB」，低通为正值。早期写成 < -40 导致永不触发。
    if slope and slope > abs(th.brick_db) and (r.get("cutoff_khz") or 99) < 22:
        tags.append("砖墙 %.0fdB/oct" % slope)
    if (r.get("clip_pct") or 0) > th.clip_pct:
        tags.append("削波 %.3f%%" % r["clip_pct"])
    tp = r.get("true_peak_dbfs")
    if tp is not None and tp > 0:
        tags.append("采样间过载 %+.1f dBTP" % tp)
    if r.get("lufs") is not None and r["lufs"] > th.loud_war_lufs:
        tags.append("响度战 %.1f LUFS" % r["lufs"])
    if r.get("lra") is not None and r["lra"] < th.lra_flat:
        tags.append("动态压平 LRA %.1f" % r["lra"])
    return tags


def advice(r: dict, th) -> str:
    """输出「能不能用它来评耳机」——本工具区别于市面产品的地方。"""
    out = []
    cut = (r.get("cutoff_hz") or 0) / 1000.0
    if cut >= 20:
        out.append("高频内容充足（到 %.1fkHz）→ 可用于评估耳机的高频延伸/空气感。" % cut)
    elif cut >= 16:
        out.append("内容止于 %.1fkHz → 评估耳机「高频延伸」不可靠：信息在源头就没了。" % cut)
    elif cut > 0:
        out.append("内容止于 %.1fkHz → 只够评估中频/齿音区，不足以判断高频。" % cut)

    b = r.get("bands_pct") or {}
    if b:
        low = b.get("sub", 0) + b.get("bass", 0)
        if low > 60:
            out.append("低频能量占 %.0f%% → 适合试低频下潜 / 控制力。" % low)
        if b.get("upper_mid", 0) > 15:
            out.append("中高频占比偏高 → 适合试齿音 / 刺耳度。")

    tp = r.get("true_peak_dbfs")
    if tp is not None and tp > 0:
        out.append("真峰值 %+.2f dBTP（超满刻度）→ DAC 重建时可能削顶，别把这点失真算到耳机头上。" % tp)
    if r.get("lufs") is not None and r["lufs"] > th.loud_war_lufs:
        out.append("集成响度 %.1f LUFS（响度战极重）→ 对比时必须先按响度对齐，否则「更响」会被误判成「更好」。"
                   % r["lufs"])
    if r.get("lra") is not None and r["lra"] < th.lra_flat:
        out.append("LRA 仅 %.1f LU（动态被压平）→ 不适合用来判断耳机的动态/微动态表现。" % r["lra"])
    if r.get("dr_db") is not None and r["dr_db"] < 6:
        out.append("DR %.1fdB 偏低 → 做音量匹配盲听时容易把「更响」误判为「更好」。" % r["dr_db"])
    if (r.get("clip_pct") or 0) > 0.01:
        out.append("存在削波 → 大动态段落本身有失真，别算到耳机头上。")
    if r.get("width_db") is not None and r["width_db"] < th.width_narrow_db:
        out.append("声道宽度 %.1f dB（近乎单声道）→ 不适合用它评声场宽度。" % r["width_db"])
    return " ".join(out)


@register
class VerdictAnalyzer(Analyzer):
    key = "verdict"
    label = "判定与建议"
    order = 90
    requires = ("basic", "spectrum")

    def run(self, ctx) -> dict:
        if not ctx.deep:           # 浅模式没有频谱，无从判定，也不要谎报"无法判定"
            return {}
        r = ctx.result
        th = ctx.thresholds
        text, level = bandwidth_verdict(ctx.fs, r.get("cutoff_hz"), th)
        tags = quality_tags(r, th)
        if tags:
            text += "｜" + "｜".join(tags)
        out = {"verdict": text, "level": level, "level_cn": LEVEL_CN.get(level, level)}
        out["advice"] = advice({**r, **out}, th)
        return out
