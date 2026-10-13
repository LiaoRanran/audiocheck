"""图表缩放 / 平移的**纯函数**。

为什么单独一个模块：缩放的数学必须能不依赖 Qt、不依赖 matplotlib 地测。
"对数轴下按倍率缩放"这种逻辑一旦写进画布类里，就只能靠肉眼验——
而它恰恰是最容易错的地方（倍率方向、锚点在哪、边界怎么钳、
对数与线性不是同一套公式）。这里每个函数都是 (数字, 数字) → (数字, 数字)，
测试可以穷举边界。

对数与线性为什么不能共用一套公式
------------------------------
线性轴上"放大 2 倍"是 `x' = a + (x - a) / 2`；
对数轴上同样一句话必须是 `x' = a · (x / a) ** (1/2)`——
否则在 20–20 kHz 这种跨三个数量级的轴上，放大后视野会一头沉、一头浮
（低频区几乎不动，高频区跑得老远）。频率轴几乎总是对数的，所以这条不是学究。
"""
from __future__ import annotations

import math

#: 滚轮一格缩放的倍率。1.25 = 每格视野缩小 20%，是地图类工具的常用值：
#: 再大一格就跳得难受，再小就要滚很多格。
ZOOM_IN = 1.25
ZOOM_OUT = 1.0 / ZOOM_IN

#: 单次平移的最大幅度（占当前视野的比例）。防"一帧甩到天际"。
PAN_MAX = 0.9


def zoom_span(x0: float, x1: float, factor: float, anchor: float, *,
              log: bool = False) -> tuple[float, float]:
    """以 `anchor` 为锚点把视野 [x0, x1] 缩放 `factor` 倍（>1 = 放大）。

    锚点定住了"鼠标指着的那一格频率"——缩放时它不动，其它内容围绕它展开。
    没有锚点（或以中心为锚）时，读数会左右漂，用户要反复重新找位置。
    """
    if not (x1 > x0) or not (factor > 0):
        return x0, x1
    if not log:
        return anchor + (x0 - anchor) / factor, anchor + (x1 - anchor) / factor
    # 对数轴：在对数空间里做同样的线性缩放。a 必须 > 0（频率轴的正半轴）。
    a = max(anchor, 1e-9)
    l0 = math.log10(max(x0, 1e-9)) - math.log10(a)
    l1 = math.log10(x1) - math.log10(a)
    return a * 10 ** (l0 / factor), a * 10 ** (l1 / factor)


def pan_span(x0: float, x1: float, frac: float, *, log: bool = False) -> tuple[float, float]:
    """把视野平移 `frac` 个"当前视野宽"（正 = 往大的方向走）。

    用**比例**而不是绝对量：拖拽时只知道"鼠标移动了多少像素"，
    换算成比例才能让同样的手势在不同缩放级别下移动同样的视觉距离。
    """
    frac = max(-PAN_MAX, min(PAN_MAX, frac))
    if not log:
        d = (x1 - x0) * frac
        return x0 + d, x1 + d
    return x0 * 10 ** frac, x1 * 10 ** frac


def clamp_span(x0: float, x1: float, lo: float, hi: float, *,
               log: bool = False) -> tuple[float, float]:
    """把视野钳回数据范围 [lo, hi]。

    超出时**贴边**而不是回弹：用户放大到边缘后继续往外拖，
    视野应该停在那里，而不是弹回中间（那会让"我拖过了"这种操作反复发生）。
    视野本身比全距还大时，直接给全距。
    """
    if not (hi > lo):
        return x0, x1
    if log:
        span = math.log10(x1) - math.log10(x0)
        full = math.log10(hi) - math.log10(lo)
        if span >= full - 1e-12:
            return lo, hi
        l_lo, l_hi = math.log10(lo), math.log10(hi)
        l0 = min(max(math.log10(max(x0, 1e-9)), l_lo), l_hi - span)
        return 10 ** l0, 10 ** (l0 + span)
    if (x1 - x0) >= (hi - lo) - 1e-12:
        return lo, hi
    x0 = min(max(x0, lo), hi - (x1 - x0))
    return x0, x0 + (x1 - x0)


def span_ratio(x0: float, x1: float, lo: float, hi: float, *, log: bool = False) -> float:
    """当前视野占数据全距的比例（对数轴按 log10 跨度算）。"""
    if hi <= lo:
        return 1.0
    if log:
        full = math.log10(hi) - math.log10(lo)
        return (math.log10(x1) - math.log10(x0)) / full if full > 0 else 1.0
    return (x1 - x0) / (hi - lo)


def zoom_step(x0: float, x1: float, lo: float, hi: float, step: int, anchor: float,
              *, log: bool = False) -> tuple[float, float]:
    """滚轮一格：`step > 0` 放大、`step < 0` 缩小，然后钳回数据范围。"""
    factor = ZOOM_IN ** max(-3, min(3, step))
    a, b = zoom_span(x0, x1, factor, anchor, log=log)
    return clamp_span(a, b, lo, hi, log=log)


def is_zoomed(x0: float, x1: float, lo: float, hi: float, *, log: bool = False) -> bool:
    """当前是不是缩放态（用来决定要不要给"复位"提示）。"""
    return span_ratio(x0, x1, lo, hi, log=log) < 0.999


def pick_ticks(lo: float, hi: float, *, log: bool = True) -> list[float]:
    """按**当前视野**挑刻度。

    为什么需要它：固定刻度表在缩放到 100–200 Hz 时只剩两个刻度，
    读者无法判断"曲线停在哪"。这里按视野宽度选一档 1/2/5 步长，
    保证任何缩放级别下都有 3–8 个主刻度——"读数能自证"在缩放后依然成立。
    """
    if hi <= lo:
        return [lo]
    if log:
        decades = math.log10(hi) - math.log10(lo)
        if decades <= 0:
            return [lo]
        if decades < 0.6:
            # 窄视野（不足 6/10 个十倍程）改用**线性**好数步长：
            # 100–200 Hz 用 1/2/5 只会得到 100 和 200 两个刻度，
            # 而这段区间恰恰是放大后最需要细读的地方。
            return pick_ticks(lo, hi, log=False)
        out: list[float] = []
        for d in range(math.floor(math.log10(lo)), math.ceil(math.log10(hi)) + 1):
            for m in (1, 2, 5):
                v = m * 10 ** d
                if lo <= v <= hi and all(abs(v - o) > 1e-9 for o in out):
                    out.append(v)
        if len(out) < 3:                       # 极端窄视野：至少给低/中/高三个参照
            out = [lo, (lo * hi) ** .5, hi]
        return sorted(out)
    raw = (hi - lo) / 6.0
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 2, 5, 10) if m * mag >= raw), default=mag * 10)
    out, v = [], math.ceil(lo / step) * step
    while v <= hi and len(out) < 12:
        out.append(round(v, 10))
        v += step
    return out
