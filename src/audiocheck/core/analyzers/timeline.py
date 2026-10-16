"""C4.3 时间定位 —— 分段扫描，把"整轨一个结论"补上"问题在哪一段"。

## 为什么需要它

带宽/响度等判定都是**整轨一个数**（频谱还是三分位取样）。而真实文件的
问题常常是**局部**的：某一段削波、某一段电平异常。整轨平均会把它们稀释
掉——"整轨 0.0% 削波"与"最后 10 秒削波 3%"给用户的行动完全不同。

## 口径（写死在这里，改它就是改结论）

* 分段：窗长 `th.seg_seconds`（默认 10 s）、重叠 `th.seg_overlap`（默认 50%）；
* 逐段统计两组数：
  * **时域**：削波比例 / 峰值 / RMS；
  * **响度**：ITU-R BS.1770 的 LUFS + 真峰值（4× 过采样）——逐段频谱的
    计算量与**区分力都还没立**（C1 的规矩），所以响度也**只报数值不判级**；
* 响度口径与整轨 `dsp.loudness` **同一套数学**（100ms 步 / 400ms 块门限积分 /
  4× 过采样真峰值，全部复用 dsp 原语，不另写一套）。但段内门限是**段级独立**
  的：段 LUFS 可与相邻段比较，**不能**与整轨 LUFS 相加或求平均（门限窗口不同）；
* 真峰值 ≥ 0 dBFS（数字满刻度硬上限，事实性判据非工程估计）⇒ 进
  `timeline_loudness_problems`；文案带"疑似"（顶到上限是事实，削峰是推断）；
* 段数 < `th.seg_min_count`（默认 4）⇒ note 里明说"不下分段结论"；
* 与整轨结论**并列**，不是替代。
"""
from __future__ import annotations

import math
import os
import tempfile

import numpy as np
import soundfile as sf
from scipy import signal

from .. import dsp
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


#: 真峰值门限（dBFS）—— ≥ 0 即已顶到上限（EBU R128 建议 ≤ −1 dBTP；
#: 顶到 0 是硬天花板，重建模拟信号可能超出满量程 ⇒ 样本间/真削峰风险）。
#: 这是事实性上限，不是工程估计。
_TP_BAD_DB = 0.0


def _tp_of(x: np.ndarray, fs: int) -> float | None:
    """段真峰值：把段写成临时 wav，复用 `dsp.true_peak` 的**标准**过采样实现
    （不另写一遍，避免与核心资产分叉）。失败返回 None（不静默降级成 0）。"""
    fd, tmp = tempfile.mkstemp(suffix=".wav", prefix="ac_seg_")
    os.close(fd)
    try:
        sf.write(tmp, x.astype(np.float32), fs, subtype="FLOAT")
        return dsp.true_peak(tmp)
    except Exception:                       # noqa: BLE001
        return None
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def segment_loudness(x: np.ndarray, fs: int) -> dict:
    """单段（x: (n, ch) float）的 ITU-R BS.1770 响度计量（集成响度 + 真峰值）。

    **复用标准原语**（`dsp.k_weighting` / `dsp._gated_loudness` / `dsp.true_peak`），
    **不重新发明标准数学**（dsp.py 是核心资产，不许改）。时序分块与 `loudness()`
    同一套：100ms 步 → 400ms 块门限积分。返回 {lufs, true_peak_dbfs}
    （任一项算不出给 None，不静默降级）。
    """
    if x.size == 0:
        return {"lufs": None, "true_peak_dbfs": None}
    ch = x.shape[1]
    (b1, a1), (b2, a2) = dsp.k_weighting(fs)
    y = np.empty_like(x, dtype=np.float64)
    z1 = [np.zeros(2) for _ in range(ch)]
    z2 = [np.zeros(2) for _ in range(ch)]
    for c in range(ch):
        xc = x[:, c].astype(np.float64)
        xc, z1[c] = signal.lfilter(b1, a1, xc, zi=z1[c])
        y[:, c], z2[c] = signal.lfilter(b2, a2, xc, zi=z2[c])
    np.square(y, out=y)

    hop = max(1, int(dsp.BLOCK_STEP * fs))
    # 段太短凑不出 4 个 100ms 步 ⇒ 只报真峰值（门限积分无意义）
    if y.shape[0] < hop * 4:
        return {"lufs": None, "true_peak_dbfs": _tp_of(x, fs)}

    nsteps = y.shape[0] // hop
    step_ms = np.empty((nsteps, ch), dtype=np.float64)
    acc = np.zeros(ch)
    acc_n = si = 0
    p = 0
    while p < y.shape[0] and si < nsteps:
        take = min(hop - acc_n, y.shape[0] - p)
        acc += y[p:p + take].sum(axis=0)
        acc_n += take
        p += take
        if acc_n == hop:
            if si < nsteps:
                step_ms[si] = acc / hop
                si += 1
            acc[:] = 0.0
            acc_n = 0
    if si < 4:
        return {"lufs": None, "true_peak_dbfs": _tp_of(x, fs)}
    step_ms = step_ms[:si]

    # 积分响度：400ms 块（4 个 100ms 步）滑窗，门限平均（与 loudness() 同一公式）
    kern = np.ones(4) / 4
    blk = np.stack([np.convolve(step_ms[:, c], kern, mode="valid")
                    for c in range(ch)], axis=1)
    lufs = dsp._gated_loudness(blk, dsp.REL_GATE_INT)
    return {"lufs": round(lufs, 1) if lufs is not None else None,
            "true_peak_dbfs": _tp_of(x, fs)}


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
            ld = segment_loudness(x, fs)
            rows.append({
                "start": round(a / fs, 1), "end": round((a + x.shape[0]) / fs, 1),
                "clip_pct": round(clips / x.size * 100.0, 4),
                "peak_dbfs": round(20.0 * math.log10(peak + 1e-9), 2) if peak > 0 else None,
                "rms_dbfs": round(20.0 * math.log10(rms + 1e-9), 2) if rms > 0 else None,
                "lufs": ld["lufs"], "true_peak_dbfs": ld["true_peak_dbfs"],
            })

        problems = [{"span": "%s–%s" % (_mmss(r["start"]), _mmss(r["end"])),
                     "start": r["start"], "end": r["end"], "clip_pct": r["clip_pct"],
                     "text": "%s–%s 削波 %.3f%%" % (_mmss(r["start"]), _mmss(r["end"]),
                                                    r["clip_pct"])}
                    for r in rows if r["clip_pct"] > th.clip_pct]

        # 逐段响度维度（ITU-R BS.1770 标准，复用 dsp 原语；只报数值不判级）
        loud_problems = [{"span": "%s–%s" % (_mmss(r["start"]), _mmss(r["end"])),
                          "start": r["start"], "end": r["end"],
                          "true_peak_dbfs": r["true_peak_dbfs"],
                          "text": "%s–%s 真峰值 %.1f dBFS（顶到上限，疑似削峰）"
                                  % (_mmss(r["start"]), _mmss(r["end"]), r["true_peak_dbfs"])}
                         for r in rows if r["true_peak_dbfs"] is not None
                         and r["true_peak_dbfs"] >= _TP_BAD_DB]

        notes = []
        if len(rows) < th.seg_min_count:
            notes.append("仅 %d 段（< %d），不下分段结论——小样本不许硬判"
                         % (len(rows), th.seg_min_count))
        if capped:
            notes.append("文件超长，只扫前 %d 段（步长 %.0fs）" % (n, hop / fs))
        if not notes:
            notes.append("时域逐段统计（削波/峰值/RMS）+ 逐段响度（ITU-R BS.1770："
                         "LUFS/真峰值）；逐段频谱未做——区分力未立，只报数值")
        if loud_problems:
            notes.append("检测到 %d 段真峰值顶到 0 dBFS（局部削峰风险）" % len(loud_problems))
        return {"timeline": rows, "timeline_problems": problems,
                "timeline_loudness_problems": loud_problems,
                "timeline_note": "；".join(notes)}
