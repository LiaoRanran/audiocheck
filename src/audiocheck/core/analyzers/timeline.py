"""C4.3 时间定位 —— 分段扫描，把"整轨一个结论"补上"问题在哪一段"。

## 为什么需要它

带宽/响度等判定都是**整轨一个数**（频谱还是三分位取样）。而真实文件的
问题常常是**局部**的：某一段削波、某一段电平异常。整轨平均会把它们稀释
掉——"整轨 0.0% 削波"与"最后 10 秒削波 3%"给用户的行动完全不同。

## 口径（写死在这里，改它就是改结论）

* 分段：窗长 `th.seg_seconds`（默认 10 s）、重叠 `th.seg_overlap`（默认 50%）；
* 逐段只做**时域**统计（削波比例 / 峰值 / RMS）——逐段频谱的计算量与
  **区分力都还没立**（C1 的规矩），所以只报数值不判级；
* 段数 < `th.seg_min_count`（默认 4）⇒ note 里明说"不下分段结论"；
* 与整轨结论**并列**，不是替代。
"""
from __future__ import annotations

import math

import soundfile as sf

from ..registry import Analyzer, register

#: 分段数上限（性能界：逐段解码全长音频的代价随段数线性增长）。
#: 超长文件从**开头**截断——步长固定，截断只影响覆盖范围，
#: 不影响已报各段的数值（截断事实写进 timeline_note）。
_MAX_SEGMENTS = 96

#: 削波检测门限 —— **与 basic.py 同一口径**（|x| ≥ 此值算削平的样本）。
_CLIP_LEVEL = 0.9999


def _mmss(t: float) -> str:
    s = int(round(t))
    return "%d:%02d" % (s // 60, s % 60)


@register
class TimelineAnalyzer(Analyzer):
    """分段扫描（削波/峰值/RMS），输出问题段。纯时域，无谱计算。"""

    key = "timeline"
    label = "时间定位"
    order = 86
    requires = ("basic",)

    def run(self, ctx) -> dict:
        if not ctx.deep:
            return {}
        frames, fs = ctx.frames, ctx.fs
        th = ctx.thresholds
        seg = int(th.seg_seconds * fs)
        if seg <= 0 or frames < seg:
            return {"timeline": [],
                    "timeline_note": "文件短于一个分段窗（%.0fs），无法分段" % th.seg_seconds}
        hop = max(1, int(seg * (1.0 - th.seg_overlap)))
        n_total = 1 + (frames - seg) // hop
        capped = n_total > _MAX_SEGMENTS
        n = min(n_total, _MAX_SEGMENTS)

        rows = []
        for i in range(n):
            a = i * hop
            x, _ = sf.read(ctx.path, start=a, frames=seg, dtype="float32", always_2d=True)
            if x.size == 0:
                break
            peak = float(abs(x).max())
            clips = int((abs(x) >= _CLIP_LEVEL).sum())
            rms = float((x.astype("float64") ** 2).mean() ** 0.5)
            rows.append({
                "start": round(a / fs, 1), "end": round((a + x.shape[0]) / fs, 1),
                "clip_pct": round(clips / x.size * 100.0, 4),
                "peak_dbfs": round(20.0 * math.log10(peak + 1e-9), 2) if peak > 0 else None,
                "rms_dbfs": round(20.0 * math.log10(rms + 1e-9), 2) if rms > 0 else None,
            })

        problems = [{"span": "%s–%s" % (_mmss(r["start"]), _mmss(r["end"])),
                     "start": r["start"], "end": r["end"], "clip_pct": r["clip_pct"],
                     "text": "%s–%s 削波 %.3f%%" % (_mmss(r["start"]), _mmss(r["end"]),
                                                    r["clip_pct"])}
                    for r in rows if r["clip_pct"] > th.clip_pct]

        notes = []
        if len(rows) < th.seg_min_count:
            notes.append("仅 %d 段（< %d），不下分段结论——小样本不许硬判"
                         % (len(rows), th.seg_min_count))
        if capped:
            notes.append("文件超长，只扫前 %d 段（步长 %.0fs）" % (n, hop / fs))
        if not notes:
            notes.append("时域逐段统计（削波/峰值/RMS）；逐段频谱未做——区分力未立，只报数值")
        return {"timeline": rows, "timeline_problems": problems,
                "timeline_note": "；".join(notes)}
