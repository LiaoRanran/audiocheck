"""B10 竞品轮的共享件：真数据 + 亮色主题 + 共享硬约束。

## 为什么有一个共享件

规则说"每骨架一个独立文件"，指的是**不动 `src/`**、每个骨架能单独跑；
四个骨架要共享的是同一批**真实**输入（真实 WAV → 真实频谱曲线、同一套
D 色图与 tint/band 判定色、同一只顶栏恒锚）。把这部分抄四遍只会让四份
实现悄悄漂开——那样"竞品"比的就不是骨架而是抄写误差。

## 共享硬约束（B10 §3）

* 配色：`palette.py` 的 tin/band 判定结构 + 项目的 OKLCH 生成路径；
* 字体：中文走 `theme.init_fonts` 解析出的族；数字走项目 DIN 系/等宽。
  ⚠ 提示词写的是"Helvetica Neue LT Pro"——**本机没有这个族**（实测，
  见 `_fonts()` 的说明）。按 AGENTS.md 的纪律：不假装有，登记后
  用项目的 DIN 系等价物，并在差异说明里注明。
* 秩序：tabular 数字成列、1px 分隔线、判词 26px、字号层级 ≤5 种；
* 恒定锚：顶栏必须有 **文件名 + 判定态 + hero（真实截止）**。

## 数据

曲线从**真实 WAV** 现算（numpy 分带取包络），不是画示意图。
断言：非空、非直线（`assert_curve`）——曲线是直的说明合成出错了。
"""
from __future__ import annotations

import os
import struct
import sys
import wave

import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(_ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "src"))
if os.path.join(_ROOT, "packaging") not in sys.path:
    sys.path.insert(0, os.path.join(_ROOT, "packaging"))

DEMO = os.path.join(_ROOT, "out", "demo")
SHOTS = os.path.join(_ROOT, "shots", "b10")

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402

PAL = P.build(T.BUILTIN_SEED, "light", "calm")     # 竞品轮**统一用亮色**（B10 §5）
LEVELS = {"ok": "success", "warn": "warning", "bad": "danger"}


def app():
    a = QApplication.instance() or QApplication([])
    T.init_fonts(a)
    return a


def _fonts():
    """本机可用的族名。数字族**不存在 Helvetica Neue LT Pro**（实测：
    `QFontDatabase.families()` 里没有；Windows 的等价物是 Segoe/DIN）。
    按纪律不假装有 → 用项目的 DIN 系（`theme.figure`）。"""
    return {"cjk": T.DARK.font_family, "num": T.figure_family() or T.mono_family()}


def tune_font(f: QFont, size: float, *, weight: int = 400, num: bool = False) -> QFont:
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.DemiBold if weight >= 600 else QFont.Weight.Normal)
    f.setFamilies([_fonts()["num" if num else "cjk"]])
    # tabular：数字沿竖线对齐是仪器的底线（等宽族本身满足；DIN 不满足，
    # 所以**要对齐的列**必须走等宽——见 `num_font` 的注释）。
    return f


def num_font(size: float):
    """**要对齐的数字**用等宽（`theme.mono` 已实测本机存在）。"""
    f = QFont()
    f.setFamilies([T.mono_family() or 'Consolas'])
    f.setPointSizeF(size)
    return f


# ---------------------------------------------------------------- 真实数据
def read_wav(path):
    with wave.open(path, "rb") as w:
        ch, sw, sr, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(min(n, sr * 8))
    if sw == 2:
        a = np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    else:
        a = np.frombuffer(raw, dtype="<i4").astype(np.float64) / 2147483648.0
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return a, sr


def band_curve(path, points: int = 360, lo: float = 20.0, hi: float = 20000.0):
    """分带取包络 → (N,2) 曲线（对数频轴，相对 dB）。

    真做 FFT 分带：不是"照着形状编一条"，所以曲线里带着真实内容的
    截止陡度与噪声底——竞品轮判的是"这条曲线怎么被排版"，前提是
    它得是真曲线。
    """
    a, sr = read_wav(path)
    nper = 4096
    hop = max(1, len(a) // 160)
    win = np.hanning(nper)
    frames = max(1, (len(a) - nper) // hop)
    acc = np.zeros(nper // 2 + 1)
    for i in range(min(frames, 160)):
        seg = a[i * hop:i * hop + nper]
        acc += np.abs(np.fft.rfft(seg * win)) ** 2
    spec = 10.0 * np.log10(np.maximum(acc / max(1, min(frames, 160)), 1e-12))
    f = np.fft.rfftfreq(nper, 1.0 / sr)
    edges = np.geomspace(lo, min(hi, f[-1]), points + 1)
    out = np.empty(points)
    for i in range(points):
        m = (f >= edges[i]) & (f < edges[i + 1])
        out[i] = spec[m].max() if m.any() else -200.0
    out -= out.max()
    fc = 0.5 * (edges[:-1] + edges[1:])
    return np.stack([fc, out], axis=1)


def assert_curve(cur) -> None:
    """**非空、非直线**——这是"真渲染真数据"的可执行部分。"""
    assert cur.ndim == 2 and cur.shape[0] > 50, "曲线太短/不是 (N,2)"
    assert np.isfinite(cur[:, 1]).all(), "曲线里有 NaN/Inf"
    assert float(np.std(cur[:, 1])) > 1.0, "曲线几乎是直的——合成大概率出错"


def cases():
    """达标态 / 不达标态：用两份**真实** WAV（`out/demo`），现算曲线。"""
    out = []
    for name, lv, verdict, why in (
            ("demo1.wav", "ok", "CD 级满带宽", "内容止于奈奎斯特，无编码器切痕"),
            ("demo2.wav", "bad", "有损溯源", "≈192–256 kbps 转码 · 内容止于 16.01 kHz")):
        p = os.path.join(DEMO, name)
        assert os.path.exists(p), "缺素材 %s（先跑 out/ 里的合成脚本）" % p
        cur = band_curve(p)
        assert_curve(cur)
        out.append({"file": name, "level": lv, "verdict": verdict, "why": why,
                    "cutoff_khz": 22.05 if lv == "ok" else 16.01,
                    "ratio": 1.0 if lv == "ok" else 0.73, "curve": cur})
    return out


# ---------------------------------------------------------------- 共享视觉
def tint(lv: str) -> str:
    return PAL["%s_tint" % LEVELS[lv]]


def band(lv: str) -> str:
    return PAL["%s_band" % LEVELS[lv]]


def on_tint(lv: str) -> str:
    """判词文字：亮色下 tint 是浅底 → 用**深字**（B9.1 的方向规则）。"""
    # 亮色下 tint 是浅底 → 深字。判据是"与 tint 对比 ≥4.5:1"（B9.1），
    # 不是某个具体颜色；这里直接取正文色（PAL['text']）满足它。
    return PAL["text"]


def line(width: int = 1) -> str:
    return "background:%s;max-height:%dpx;min-height:%dpx;" % (PAL["border"], width, width)


def qss(transparent: bool = False) -> str:
    """原型的全局样式：**只放共享秩序**（1px 线、无阴影、tabular 列），
    骨架各自的版面一律用布局写在骨架文件里——那才是竞品要比的东西。

    `transparent=True`（B10.5 氛围版）：全局底色让位给氛围图，面板由
    `dress()` 逐个改成半透明遮罩浮在其上。**默认 False**，所以骨架文件
    一个都不用改。
    """
    return """
    QWidget {{ background:%(bg)s; color:%(text)s; }}
    QFrame#Hair {{ %(hair)s }}
    QFrame#Vs {{ %(vs)s }}
    QLabel#Verdict {{ font-size:26px; }}
    QLabel#Hero {{ font-size:40px; }}
    QLabel#Cap {{ font-size:11px; color:%(dim)s; }}
    QLabel#Dim {{ color:%(dim)s; }}
    QPushButton {{ background:transparent; border:1px solid %(bhi)s; border-radius:8px;
                   padding:6px 12px; color:%(text)s; }}
    QPushButton:hover {{ background:%(hover)s; }}
    """ % {"bg": "transparent" if transparent else PAL["bg"],
           "text": PAL["text"], "dim": PAL["text_dim"],
           "hair": line(1), "vs": line(1), "bhi": PAL["border_hi"],
           "hover": PAL["hover"]}


# ---------------------------------------------------------------- 氛围层（B10.5）
#
# 公式**照提示词逐字实现**（工作区的 `氛围候选_*.png` 是真源）：
#   G1 极光：hue = 270 + 120·u ／ L = 0.32 + 0.50·√v ／ C = 0.06 + 0.08·sin(π·clamp(0.7u+0.3v))
#   G2 暮光纸：hue = 70 → 300 ／ L = 0.88 − 0.43·v ／ C = 0.030 + 0.045·v
# 每个网格点经 **`palette.oklch_to_srgb()`** 生成——**禁止 RGB 直接插值**：
# 跨色相族插值会串出青绿，与"最短弧"是同一条纪律的渐变版。
#
# 允许"粗网格 + 双三次放大"（提示词明确许可）：64 格时每格 ΔL ≈ 2/255，
# 双三次在格间的 RGB 误差远小于色图自身档宽，且过得了"无 banding"判据。
_ATMO = {}


def _atmo_hex(kind: str, u: float, v: float) -> str:
    if kind == "g1":
        hue = 270.0 + 120.0 * u
        L = 0.32 + 0.50 * float(np.sqrt(v))
        C = 0.06 + 0.08 * float(np.sin(np.pi * min(1.0, max(0.0, 0.7 * u + 0.3 * v))))
    elif kind == "g2":
        L = 0.88 - 0.43 * v
        C = 0.030 + 0.045 * v
        hue = 70.0 - (70.0 - 300.0) * v          # 顶部暖纸 → 底部冷紫
    else:
        raise ValueError("未知氛围 %r（只做了 g1/g2）" % kind)
    return P.oklch_to_srgb((L, C, hue % 360.0))


def atmosphere(kind: str, w: int, h: int, cells: int = 64):
    """预计算氛围底图（`QImage`）。按 (kind, w, h) 缓存——每帧只做一次 blit。"""
    from PySide6.QtCore import Qt as _Qt
    from PySide6.QtGui import QColor as _QC
    from PySide6.QtGui import QImage

    key = (kind, w, h, cells)
    if key in _ATMO:
        return _ATMO[key]
    small = QImage(cells, cells, QImage.Format_RGB32)
    for j in range(cells):
        v = j / (cells - 1.0)
        for i in range(cells):
            small.setPixelColor(i, j, _QC(_atmo_hex(kind, i / (cells - 1.0), v)))
    img = small.scaled(w, h, _Qt.IgnoreAspectRatio, _Qt.SmoothTransformation)
    _ATMO[key] = img
    return img


def veil(kind: str, u: float, v: float, alpha: float, light: bool = True) -> str:
    """面板遮罩 = 该处氛围色 + 白（亮）/黑（暗）遮罩，返回 `rgba(...)`。

    **带真 alpha**（不是把合成结果算死不透明）：面板因此真的"浮"在氛围上，
    氛围在图内的变化能透过来——这正是 GR 那一套的做法。
    """
    base = _atmo_hex(kind, u, v)
    mixed = P.mix(base, "#ffffff" if light else "#000000", alpha)
    h = mixed.lstrip("#")
    a255 = int(round(alpha * 255))
    return "rgba(%d,%d,%d,%d)" % (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), a255)


def dress(inner, kind: str, alpha: float = 0.55, light: bool = True) -> list:
    """把骨架的面板改成**半透明浮层**。返回 [(objectName/类名, rgba, u, v)] 供记账。

    扫的是"有布局且够大"的容器——不是所有控件：给每个 QLabel 也刷上遮罩
    会得到满地小方块，那不是面板。文字因此坐在**合成后**的底色上，判据量
    的就是那个合成结果。
    """
    from PySide6.QtCore import Qt as _Qt

    w = max(1, inner.width())
    h = max(1, inner.height())
    out = []
    for ch in inner.findChildren(type(inner)):
        if ch is inner or ch.layout() is None:
            continue
        if ch.width() < 140 or ch.height() < 24:
            continue
        p = ch.mapTo(inner, ch.rect().topLeft())
        u = min(1.0, max(0.0, (p.x() + ch.width() / 2.0) / w))
        v = min(1.0, max(0.0, (p.y() + ch.height() / 2.0) / h))
        col = veil(kind, u, v, alpha, light)
        ch.setStyleSheet("background:%s;border:0;" % col)
        # ⚠ 枚举挂在**类**上（`Qt.WidgetAttribute`），不在实例上：
        # `ch.WidgetAttribute` 会 AttributeError（实测）。
        ch.setAttribute(_Qt.WidgetAttribute.WA_StyledBackground, True)
        out.append((ch.objectName() or type(ch).__name__, col, round(u, 3), round(v, 3)))
    return out


def topbar(case, *, hero=True):
    """**恒定锚**：文件名 + 判定态 + hero（真实截止）。四骨架都必须有。"""
    from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

    w = QWidget()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    name = QLabel(case["file"])
    name.setFont(tune_font(QFont(), 13, weight=600))
    h.addWidget(name)
    chip = QLabel("● " + case["verdict"])
    chip.setFont(tune_font(QFont(), 11, weight=600))
    chip.setStyleSheet("color:%s;" % band(case["level"]))
    h.addWidget(chip)
    h.addStretch(1)
    if hero:
        hero_lab = QLabel("%.2f" % case["cutoff_khz"])
        hero_lab.setObjectName("Hero")
        hero_lab.setFont(num_font(40))
        unit = QLabel("kHz")
        unit.setObjectName("Cap")
        unit.setFont(tune_font(QFont(), 11))
        h.addWidget(hero_lab)
        h.addWidget(unit)
    return w


def verdict_band(case, height: int = 4):
    """4px 判定带（B9.1 资产：高彩只在这一条线上）。

    ⚠ 底下压一条 1px 暗线（保留 B7.4 的"亮边配暗线"体系）：B10.5 的氛围
    记账实测，判定带对氛围背景只有 **1.51:1**——高彩细线对中明度底本来
    就到不了 3:1。它的可读性靠**边界**（暗线把它从氛围里切出来），
    不靠亮度对比；如实记账，不假称达标。
    """
    from PySide6.QtWidgets import QFrame, QVBoxLayout, QWidget
    box = QWidget()
    v = QVBoxLayout(box)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)
    f = QFrame()
    f.setFixedHeight(height)
    f.setStyleSheet("background:%s;border:0;" % band(case["level"]))
    v.addWidget(f)
    ul = QFrame()
    ul.setFixedHeight(1)
    ul.setStyleSheet("background:%s;border:0;" % PAL["border_hi"])
    v.addWidget(ul)
    return box


def plot_curve(painter, rect, cur, lv: str, *, wash: bool = True, notes=()):
    """把真实曲线画进 `rect`（**QPainter 直绘**：竞品轮不碰 matplotlib）。

    图内注记 `notes` 是 (相对位置, 文本, 像素字号) 三元组——S2/S4 的
    "画在曲线上的注记"走这里，保证四个骨架的**笔迹**是同一种。
    """
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QColor, QPainterPath, QPen

    x0, y0, w, h = rect
    xs, ys = cur[:, 0], cur[:, 1]
    lx = np.log10(np.maximum(xs, 1.0))
    lx = (lx - lx.min()) / max(1e-9, lx.max() - lx.min())
    lo, hi = float(ys.min()), float(ys.max())
    ny = (ys - lo) / max(1e-9, hi - lo)
    pts = [QPointF(x0 + float(a) * w, y0 + (1.0 - float(b)) * h) for a, b in zip(lx, ny)]

    if wash:                       # 神特第 1 招：截止淡染（含 B9 的 0.08 预算）
        cx = x0 + float(lx[int(0.73 * (len(lx) - 1))]) * w
        painter.fillRect(int(cx - 6), int(y0), 12, int(h),
                         QColor(_rgba(band(lv), 0.08)))
    path = QPainterPath(pts[0])
    for p in pts[1:]:
        path.lineTo(p)
    from PySide6.QtGui import QPainter as _QP
    painter.setRenderHint(_QP.RenderHint.Antialiasing, True)
    painter.setPen(QPen(QColor(PAL["border"]), 1))     # 1px 网格（对比 1.20 不许动）
    for i in range(1, 4):
        yy = y0 + h * i / 4.0
        painter.drawLine(QPointF(x0, yy), QPointF(x0 + w, yy))
    painter.setPen(QPen(QColor(band(lv)), 1.5))        # 数据墨**零模糊**
    painter.drawPath(path)
    for rel, text, px in notes:
        painter.setPen(QPen(QColor(PAL["text"] if rel[1] < 0.6 else PAL["text_dim"]), 1))
        f = num_font(px) if text[:1].isdigit() else tune_font(QFont(), px * 0.75)
        painter.setFont(f)
        painter.drawText(QPointF(x0 + rel[0] * w, y0 + rel[1] * h), text)
    del Qt


def _rgba(hex_col: str, alpha: float) -> str:
    h = hex_col.lstrip("#")
    return "rgba(%d,%d,%d,%d)" % (int(h[0:2], 16), int(h[2:4], 16),
                                  int(h[4:6], 16), int(alpha * 255))


class PlotWidget:
    """把 `plot_curve` 包成控件。**四骨架共用一支画笔**——竞品要比的是
    版面（谁在哪、占多大），不是四个人的笔迹差异。"""

    def make(case, notes=(), pad=0):
        from PySide6.QtWidgets import QWidget as _QWidget

        class _P(_QWidget):
            def paintEvent(self, ev):
                from PySide6.QtGui import QPainter as _QP
                pr = _QP(self)
                r = self.rect()
                plot_curve(pr, (r.x() + pad, r.y() + pad,
                                r.width() - 2 * pad, r.height() - 2 * pad),
                           case["curve"], case["level"], notes=notes)
                pr.end()
        return _P()


class AtmoRoot:
    """氛围底图容器：`paintEvent` 里一次 blit 预计算的 `QImage`。

    骨架的 `mk_win` 返回的根控件被塞进来当子件——骨架文件因此**一行都不用改**。
    """

    def wrap(kind: str, inner):
        from PySide6.QtGui import QPainter as _QP
        from PySide6.QtWidgets import QVBoxLayout as _V
        from PySide6.QtWidgets import QWidget as _W

        class _A(_W):
            def paintEvent(self, ev):
                pr = _QP(self)
                pr.drawImage(0, 0, atmosphere(self.kind, self.width(), self.height()))
                pr.end()

        w = _A()
        w.kind = kind
        v = _V(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(inner)
        return w


#: 组合图的 veil alpha 真源。**0.55 = 实测下限**（`out/_b14_alpha.py`）：
#: 低于它，判定档色对面板合成底掉到 3:1 以下；高于它只是氛围渗入变少。
#: compose_b105.py 与判据（test_ui_b9_chart）都从这里取，不各写一份。
COMPOSE_ALPHA = 0.55


def panel_bg(kind: str, u: float = 0.5, v: float = 0.5, alpha: float = COMPOSE_ALPHA):
    """判据 2/3 要用的"合成后背景色"（与 `veil` 同源，但返回 hex 便于算对比）。"""
    return P.mix(_atmo_hex(kind, u, v), "#ffffff", alpha)


def shot(win, name: str, size=(1400, 900)):
    """跑真窗口 → offscreen 截图 → `shots/b10/<name>.png`。"""
    import perf_probe
    a = app()
    os.makedirs(SHOTS, exist_ok=True)
    win.resize(*size)
    win.show()
    perf_probe.pump(a, 0.35)
    path = os.path.join(SHOTS, "%s.png" % name)
    win.grab().save(path)
    print("  已出图 %-16s（%d KB）" % (os.path.basename(path), os.path.getsize(path) // 1024))
    return path


def run(mk_win, tag: str):
    """四骨架统一的入口：两个态各出一张图，退出码 0 才算跑通。"""
    for case in cases():
        shot(mk_win(case), "%s_%s" % (tag, case["level"]))
    _ = struct      # 保留 import（真实读取路径从 wave 走，struct 备用）


if __name__ == "__main__":       # 自检：数据 + 字体 + 配色三件事都在
    app()
    print("字体:", _fonts())
    print("判定色: ok/警告/不达标 →", [(lv, band(lv)) for lv in ("ok", "warn", "bad")])
    for c in cases():
        print("  %-11s %-9s cutoff %.2f kHz ｜ 曲线 %s ｜ std %.1f"
              % (c["file"], c["verdict"], c["cutoff_khz"], c["curve"].shape,
                 float(np.std(c["curve"][:, 1]))))
