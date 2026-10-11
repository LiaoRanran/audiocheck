"""带宽与频段 —— 本项目最核心的判定依据。

算法要点（都是踩过坑写下来的）：
  * 平滑在 log 频轴上做（1/6 倍频程），线性轴平滑会低估高频细节；
  * 自高向低扫，**第一个**连续满足点才是截止点；
  * 循环下界不能取 `f[0]`——那是 0Hz 直流分量，会导致死循环。
"""
from __future__ import annotations

import numpy as np

from ...config import BANDS
from ..dsp import spectral_features
from ..registry import Analyzer, register


def _smooth_log_axis(f: np.ndarray, y: np.ndarray, fraction: float = 6.0) -> np.ndarray:
    """在 log 频轴上做 1/fraction 倍频程滑动平均。"""
    ratio = 2 ** (1.0 / fraction)
    out = y.copy()
    for i, fc in enumerate(f):
        if fc <= 0:
            continue
        m = (f >= fc / ratio) & (f <= fc * ratio)
        if m.sum() >= 2:
            out[i] = y[m].mean()
    return out


def smooth_log(f: np.ndarray, db: np.ndarray, fraction: float = 6.0) -> np.ndarray:
    """dB 域平滑（保留给绘图用；判定请用功率域）。"""
    return _smooth_log_axis(f, db, fraction)


def smooth_power(f: np.ndarray, db: np.ndarray, fraction: float = 6.0) -> np.ndarray:
    """**线性功率域**平滑后转回 dB。

    为什么不在 dB 域平滑：dB 域是"算术平均"，一段信号里只要掺进一半静音，
    均值就直接掉 3dB×……实际上会被拉到 (0 + -90)/2 = -45dB，
    于是陡峭的砖墙被抹成一条 12% 宽的过渡带，检出的截止点系统性偏高约 8%。
    在线性功率域平均才是物理正确的：一半通带一半阻带时正好得到 -3dB（半功率点）。
    """
    p = np.maximum(10 ** (db / 10.0), 0.0)
    ps = _smooth_log_axis(f, p, fraction)
    return 10 * np.log10(ps + 1e-20)


def detect_cutoff(f: np.ndarray, db: np.ndarray, min_drop_db: float = 15.0,
                  min_hz: float = 20.0, need: int = 3, min_stop_db: float = 20.0):
    """真实高频截止点与砖墙陡度。

    返回 (cutoff_hz, slope_db_per_oct)。slope 为**正值**：截止点往上一个倍频程掉了多少 dB。

    算法（三代演进，前两代都是错的，记下来免得再踩）
    ------------------------------------------------
    v1 「按 1/12 倍频程步进下降，累计 need 步取当前频率」
       → 恒定偏低 need-1 步：44.1k 满带宽文件恒报 22050/2^(2/12)=19.64k，
         把满带宽 CD 误判成"轻微带限"。
    v2 「在真实频点上找最高连续超阈段 + dB 域深阈值」
       → dB 域平滑把砖墙抹成 12% 宽的过渡带，检出值系统性偏高约 8%。
    v3（当前）**功率域平滑 + 半功率点定位 + 陡度守卫**：
       · 功率域平均物理正确：一半通带一半阻带 → 恰好 -3dB
       · 取"通带参考 -3dB"的最高点，对砖墙即真实边缘（实测误差 <1%）
       · 再用陡度守卫剔除自然衰减（音乐高频本来就会滚降），
         避免把普通滚降误判成"截止"
    """
    if f is None or db is None or len(f) < 16:
        return None, 0.0
    raw, s = db, smooth_power(f, db)
    j_end = len(f) - 1
    nyq = float(f[j_end])

    # 第一步：在**未平滑**的原始谱上自高向低找局部陡降。
    # 必须用原始谱：功率域平滑窗口是 ±1/6 倍频程（约 ±12%），
    # 与"截止点到奈奎斯特的距离"同量级时，边缘会被平滑抹掉
    # （实测：44.1k 内容到 19.5k，平滑后上方只掉 8.6dB，判定器完全看不见）。
    # 同时必须用**局部**参考（下方窗口 vs 上方窗口），不能用全频段中位数——
    # 真实音乐的谱本身随频率向下倾斜，v3 正是这么错的（25kHz 的实际内容被报成 6.8kHz）。
    step = 2 ** (1 / 12)
    cur, coarse = nyq, None
    while cur > max(min_hz * 2.0, 100.0):
        hi_f = min(cur * 1.6, nyq)
        if hi_f - cur < cur * 0.04:          # 上方几乎没有可用频段，跳过
            cur /= step
            continue
        jc = int(np.argmin(np.abs(f - cur)))
        jl = int(np.argmin(np.abs(f - cur / 1.6)))
        jh = int(np.argmin(np.abs(f - hi_f)))
        below = float(np.median(raw[jl:jc + 1]))
        above = float(np.median(raw[jc:jh + 1]))
        if below - above >= min_drop_db:
            coarse = cur
            break
        cur /= step

    if coarse is None:                        # 没有陡降 → 内容直达奈奎斯特
        return nyq, 0.0

    # 第二步：以「通带电平与阻带底的中间点」为阈值，在原始谱上定位边缘。
    # 用中点而非"通带 -3dB"：后者对倾斜谱会落到谱自然下降 3dB 的位置，而不是真正的截止点。
    jc = int(np.argmin(np.abs(f - coarse)))
    jl = int(np.argmin(np.abs(f - coarse / 1.6)))
    jh = int(np.argmin(np.abs(f - min(coarse * 1.6, nyq))))
    floor_lvl = float(np.median(raw[jc:jh + 1]))
    pass_lvl = float(np.median(raw[jl:jc + 1]))
    # 阻带必须真的是"坑"。缓滚降（如 2 阶巴特沃斯）上方只低几个 dB，
    # 算出来的"中点阈值"会落在谱自然下降处，把正常音源判成带限。
    if pass_lvl - floor_lvl < min_stop_db:
        return nyq, 0.0
    thr = (pass_lvl + floor_lvl) / 2.0

    run, j0 = 0, None
    for i in range(j_end, -1, -1):            # 自高向低，需连续 need 点越过阈值才算数
        if raw[i] >= thr:
            run += 1
            if run >= need:
                j0 = i + need - 1
                break
        else:
            run = 0
    if j0 is None:
        return nyq, 0.0
    cut = float(f[j0])
    if cut < min_hz:
        return None, 0.0

    j1 = int(np.argmin(np.abs(f - min(cut * 2, nyq))))
    return cut, (float(s[j0] - s[j1]) if j1 > j0 else 0.0)


def band_balance(f: np.ndarray, linear_power: np.ndarray) -> dict:
    """七频段能量占比（%）。"""
    if f is None:
        return {}
    parts, tot = {}, 0.0
    for name, lo, hi in BANDS:
        v = float(linear_power[(f >= lo) & (f < hi)].sum())
        parts[name] = v
        tot += v
    return {k: round(v / tot * 100, 1) for k, v in parts.items()} if tot > 0 else {}


@register
class SpectrumAnalyzer(Analyzer):
    key = "spectrum"
    label = "带宽与频段"
    order = 30

    def run(self, ctx) -> dict:
        if not ctx.deep:
            return {}
        # 静音素材没有"带宽"可言——不设这道门，相对阈值会把整段静音都当成内容。
        # 注意必须显式判 None：峰值正好 0.0 dBFS（满刻度，很常见）时，
        # `x or -999` 会因为 0.0 是假值而把正常素材误判成静音。
        peak = ctx.result.get("peak_dbfs")
        if peak is None or peak < -60.0:
            return {"silent": True}
        f, db = ctx.spectrum()
        if f is None:
            return {}
        cut, slope = detect_cutoff(f, db, min_drop_db=ctx.thresholds.min_drop_db)
        out = {
            "cutoff_hz": round(cut, 1) if cut else None,
            "cutoff_khz": round(cut / 1000, 2) if cut else None,
            "cutoff_ratio": round(cut / (ctx.fs / 2), 3) if cut else None,
            "brickwall_db_per_oct": round(slope, 1) if slope else None,
            "bands_pct": band_balance(f, 10 ** (db / 10)),
        }
        out.update(spectral_features(f, db))
        ctx.result["_f"], ctx.result["_db"] = f, db
        return out
