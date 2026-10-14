"""氛围层（B14 §3）：把 `b13_front.py` 判过的 G1 极光 + 面板 veil 搬进真程序。

## 与原型同源

公式与 `prototypes/_kit.py` 逐字同源（工作区 `氛围候选_*.png` 是真源）。
颜色一律经 `palette.oklch_to_srgb` 生成——**禁 RGB 直插**：跨色相族插值会串出
青绿，与"最短弧"是同一条纪律的渐变版（B12 教训）。

## 与项目既有约定的关系（重要）

* 面板**不加 `QGraphicsDropShadowEffect`**（`material.py` 已论证：与 motion 的
  opacity 冲突、会静默消失）；内容区卡片**本就不该有投影**（`material.py` 文档：
  "投影只给浮层用"）。所以氛围层的"深度"靠 **氛围 + 边框 + detailsBg**，
  **不靠给内容面板加投影**——这是原型与真程序的一处有意分歧，按真程序约定走。
* 氛围 alpha 随底实测亮度（`veil_alpha`），不是常数（B14 D-light）。
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QWidget

from . import palette as P

#: 默认氛围：G1 极光（用户 2026-10-14 判"最耐看"）。
DEFAULT_KIND = "g1"
#: 面板 veil 基准浓度。**下限由曲线对比决定**（B14 §12：曲线 ≥3:1 要求 α≥0.55）。
COMPOSE_ALPHA = 0.55
_CACHE: dict = {}


def atmo_hex(kind: str, u: float, v: float) -> str:
    """氛围层在归一化坐标 (u,v) 处的颜色（hex）。"""
    if kind == "g1":
        hue = 270.0 + 120.0 * u
        level = 0.32 + 0.50 * math.sqrt(v)
        chroma = 0.06 + 0.08 * math.sin(math.pi * min(1.0, max(0.0, 0.7 * u + 0.3 * v)))
    elif kind == "g2":
        level = 0.88 - 0.43 * v
        chroma = 0.030 + 0.045 * v
        hue = 70.0 - (70.0 - 300.0) * v
    else:
        raise ValueError("未知氛围 %r（只做了 g1/g2）" % kind)
    return P.oklch_to_srgb((level, chroma, hue % 360.0))


def veil_alpha(u: float, v: float, kind: str = DEFAULT_KIND) -> float:
    """面板遮罩浓度 = f(底实测亮度)（D-light）：底越暗抹白越多，面板稳定偏亮。"""
    base_l = P.srgb_to_oklch(atmo_hex(kind, u, v))[0]
    return min(0.93, max(0.76, 0.86 + 0.30 * (0.55 - base_l)))


def panel_hex(u: float, v: float, alpha: float | None = None,
              kind: str = DEFAULT_KIND, light: bool = True) -> str:
    """面板**合成后**的实底色（veil 叠到氛围上）——给 QSS 用（不透明，避免双层 alpha）。"""
    a = veil_alpha(u, v, kind) if alpha is None else alpha
    return P.mix(atmo_hex(kind, u, v), "#ffffff" if light else "#000000", a)


def atmosphere_image(kind: str, w: int, h: int, cells: int = 64) -> QImage:
    """预计算氛围底图（粗网格 + 双三次放大，格间误差远小于色图档宽）。"""
    key = (kind, w, h, cells)
    if key in _CACHE:
        return _CACHE[key]
    small = QImage(cells, cells, QImage.Format_RGB32)
    for j in range(cells):
        v = j / (cells - 1.0)
        for i in range(cells):
            small.setPixelColor(i, j, QColor(atmo_hex(kind, i / (cells - 1.0), v)))
    img = small.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    _CACHE[key] = img
    return img


class AtmosphereHost(QWidget):
    """氛围底容器：`paintEvent` 一次 blit 预计算的 `QImage`；内容作为子件浮其上。"""

    def __init__(self, parent=None, kind: str = DEFAULT_KIND):
        super().__init__(parent)
        self.kind = kind
        self.setObjectName("AtmosphereHost")
        # 让子件的透明区域透出氛围（否则默认底色会盖住）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def paintEvent(self, _):                            # noqa: N802
        pr = QPainter(self)
        pr.drawImage(0, 0, atmosphere_image(self.kind, self.width(), self.height()))
        pr.end()
