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
                  min_hz: float = 20.0):
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
    s = smooth_power(f, db)
    j_end = len(f) - 1

    # 通带参考：200Hz–10kHz 中位数（避开低频隆隆与高频自然衰减）
    band = (f >= 200) & (f <= 10000)
    if band.sum() < 4:
        return None, 0.0
    ref = float(np.median(s[band]))

    # 半功率点：从高往低第一个仍处于"通带电平 -3dB 以内"的频点
    idx = np.where((s >= ref - 3.0) & (f >= min_hz))[0]
    if not idx.size:
        return None, 0.0
    j0 = int(idx[-1])
    cut = float(f[j0])

    # 陡度守卫：截止点之上 1/3 倍频程内的跌幅必须够大，否则是自然滚降而非截止
    jt = int(np.argmin(np.abs(f - min(cut * 1.26, f[j_end]))))
    drop = s[j0] - s[jt]
    if j0 >= j_end or drop < min_drop_db:
        # 两种情况必须分开，否则会把"升频"漏判成"真 Hi-Res"：
        #   (a) 真的没有截止（内容直达奈奎斯特，顶部没有深坑）
        #   (b) 截止太贴近奈奎斯特，1/3 倍频程里塞不下明显的落差
        # 用「顶部 5% 频段相对通带参考的落差」区分。顶部有深坑就是 (b)。
        top = f >= f[-1] * 0.95
        gap = ref - float(np.max(s[top])) if top.any() else 0.0
        if gap < 40.0:
            return float(f[j_end]), 0.0
        # 确认存在截止但定位被平滑拖高 → 换更细的平滑重新找半功率点
        fine = smooth_power(f, db, 24.0)
        idx2 = np.where((fine >= ref - 3.0) & (f >= min_hz))[0]
        if idx2.size:
            j0 = int(idx2[-1])
            cut = float(f[j0])
        return cut, 0.0

    j1 = int(np.argmin(np.abs(f - min(cut * 2, f[j_end]))))
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
        # 静音素材没有"带宽"可言——不设这道门，相对阈值会把整段静音都当成内容
        if (ctx.result.get("peak_dbfs") or -999.0) < -60.0:
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
