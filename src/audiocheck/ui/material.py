"""材质：投影 / 抗色带噪点。两条都是**自绘 + 缓存**，都不引入效果对象。

## 为什么不用 `QGraphicsDropShadowEffect`

Qt 的 QSS 没有 `box-shadow`（实测：写了不报错、也不生效），所以阴影必须
自己实现。有两条路：

1. `QGraphicsDropShadowEffect`——一行搞定，但**一个部件同时只能有一个
   graphics effect**。而这个项目里 Toast 的进出场要挂 `QGraphicsOpacityEffect`
   （`motion.fade`），两者会互相顶掉；更麻烦的是 `motion` 的清理路径结束时会
   `setGraphicsEffect(None)`，阴影会**在某次淡入之后悄悄消失**，
   而且不报错。这类"看起来做了、过一会儿没了"的失败在本项目里出现过三次
   （色条不回收、换肤不重刷、QSS 多族回退），不值得再来一次。
2. 自己在 `paintEvent` 里画——多写二十行，但**与效果对象完全正交**，
   而且可以缓存成一张位图，只在尺寸/主题变化时重画。

选 2。

## 为什么噪点要"算"而不是"贴图"

抗色带的噪点必须是**确定性**的：每次启动的噪点图案都得一样，否则截图对比
（`packaging/shot*.ps1`）会出现"明明没改却像素不同"的假差异，把真正的回归
淹掉。所以用固定种子的伪随机，不用 `os.urandom`、也不用系统噪声纹理。

## 两个东西都只许进静态层

噪点覆盖在**侧边栏与标题栏**这种"一屏里不变的大块渐变"上——那是唯一真正
会出色带的地方（纯色不会色带，渐变色带；曲线区域已经有数据本身在做视觉噪声）。
投影只给**浮层**用（Toast / 菜单 / 命令面板）。内容区的卡片不加投影：
18 张卡在同一个平面里，本来就没有高低之分，加了反而暗示不存在的层级。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from . import palette as P

#: 噪点的透明度量级。**≤2/255**（提示词给的上限）——它的作用是打散
#: 显示器 6 bit 量化造成的色带，不是让画面有颗粒感。超过 3/255 就开始"发脏"。
NOISE_ALPHA = 2
#: 噪点平铺边长。128 够大（肉眼看不出重复），又是缓存友好的一张小图。
NOISE_TILE = 128
#: 固定种子：图案必须可复现（见模块 docstring）。
NOISE_SEED = 0x5EED


def _tile(alpha: int = NOISE_ALPHA) -> QPixmap:
    """生成一张确定性的噪点平铺图（只算一次，全局共用）。"""
    img = QImage(NOISE_TILE, NOISE_TILE, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    # 线性同余：几百个像素不值得引 random 模块，而且这样**跨版本可复现**
    # （Python 的 random 实现随版本可能变，LCG 不会）。
    state = NOISE_SEED
    for y in range(NOISE_TILE):
        for x in range(NOISE_TILE):
            state = (1103515245 * state + 12345) & 0x7FFFFFFF
            # 只用最高位：噪点是 ±1 bit 的抖动，不是灰度云
            v = 0 if (state >> 20) & 1 else 255
            img.setPixelColor(x, y, QColor(v, v, v, alpha))
    return QPixmap.fromImage(img)


_TILE_CACHE: QPixmap | None = None


def noise_tile() -> QPixmap:
    global _TILE_CACHE
    if _TILE_CACHE is None:
        _TILE_CACHE = _tile()
    return _TILE_CACHE


def paint_noise(widget: QWidget, painter: QPainter | None = None) -> None:
    """把噪点平铺到控件上。**在控件自己的 `paintEvent` 末尾调用。**

    为什么不做成"覆盖层子控件"（第一版就是那么写的）：一个半透明的子控件
    需要 `WA_TranslucentBackground`，而那个属性会**强制整个顶层窗口走 alpha
    合成**——代价落在每一次重绘上。实测同一台机器：做成覆盖层时
    悬停 19.6 ms、换肤 1178 ms；改成在父控件里自绘后（见 CHANGELOG 的
    基准小节）两者都回到基线附近。噪点本来就只有 2/255 的幅度，
    为它给整窗加一层合成本末倒置。

    直接画在父控件里的另一个好处：**顺序天然正确**（先由 Qt 画 QSS 背景、
    再画噪点、最后画子控件），不需要靠"第一个创建"这种隐式约定。

    它是**静态层**：只随尺寸与主题变化，不跟鼠标、不跟数据。
    """
    if widget.width() <= 0 or widget.height() <= 0:
        return
    own = painter is None
    p = QPainter(widget) if own else painter
    p.drawTiledPixmap(widget.rect(), noise_tile())
    if own:
        p.end()


def render_shadow(w: int, h: int, radius: int, tier: str, color: str,
                  dpr: float = 1.0) -> QPixmap:
    """把"圆角矩形的柔和投影"渲染成一张位图（缓存用）。

    模糊怎么做：`QPainter` 没有高斯模糊，用**同心层叠**近似——从外到内画
    一圈圈放大的圆角矩形，透明度按二次曲线收敛（近处实、远处淡）。

    关键是用 `CompositionMode_Source` 而不是默认的 SourceOver：
     SourceOver 会把每层的 alpha **累加**起来，于是 32 层的 far 档在边缘
     (实测 alpha 231) 反而比 12 层的 near 档(214)更暗——"更远的光源投出更淡的
     影子"这条常识被叠层算法吃掉了。Source 是**覆盖**：距离 d 处的最终 alpha
     就是那条曲线在 d 处的值，与层数无关。实测近处 far 89 < near 96、
     远处 far 铺满 30px 而 near 只到 12px。

    步长取 0.25px 而不是 1px：二次曲线在近端变化快（相邻 1px 可以差 17/255），
     1px 步进会看到一圈圈同心环。细分 + 抗锯齿把环抹掉。

    `color` 传的是 `rgba(r,g,b,a)`；alpha 取它自己的值，形状由曲线决定。
    """
    cfg = P.SHADOW_TIERS[tier]
    blur, dx, dy = cfg["blur"], cfg["dx"], cfg["dy"]
    rgb, _a = _split_rgba(color)
    # 画布要装得下"本体 + 四周的羽化 + 偏移"
    pad = blur + max(abs(dx), abs(dy))
    pw, ph = w + pad * 2, h + pad * 2
    pm = QPixmap(int(pw * dpr), int(ph * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(Qt.transparent)

    pr = QPainter(pm)
    pr.setRenderHint(QPainter.Antialiasing, True)
    pr.setPen(Qt.NoPen)
    pr.setCompositionMode(QPainter.CompositionMode_Source)
    body = QRectF(pad + dx, pad + dy, w, h)
    step = 0.25
    n = int(blur / step)
    for k in range(n, 0, -1):
        d = k * step
        t = d / float(blur)
        a = cfg["alpha"] * (1.0 - t) ** 2
        if a <= 0.002:
            continue
        pr.setBrush(QColor(rgb[0], rgb[1], rgb[2], int(round(a * 255))))
        pr.drawRoundedRect(body.adjusted(-d, -d, d, d), radius + d, radius + d)

    # **把卡片自己那一块抠掉**。上面每一层都是"本体的同心放大"，而放大后的
    # 矩形**包含**本体——不抠的话卡片位置上会盖一层最内圈的墨（实测 alpha=214），
    # 卡片底下透出一片灰，圆角处最明显。
    # 用 CompositionMode_Clear 挖洞，而不是"只画环"：环状绘制在圆角与
    # 羽化的交界会留下阶梯状的锯齿。
    pr.setCompositionMode(QPainter.CompositionMode_Clear)
    pr.setBrush(Qt.black)
    pr.drawRoundedRect(body, radius, radius)
    pr.setCompositionMode(QPainter.CompositionMode_SourceOver)
    pr.end()
    return pm


def _split_rgba(color: str) -> tuple[tuple[int, int, int], float]:
    """把 "rgba(r,g,b,a)" 拆成 ((r,g,b), a)。解析不了就退回冷黑。"""
    try:
        nums = [x.strip() for x in color[color.index("(") + 1:color.rindex(")")].split(",")]
        return (int(float(nums[0])), int(float(nums[1])), int(float(nums[2]))), float(nums[3])
    except (ValueError, IndexError):
        return (2, 6, 23), 0.4


class ShadowFrame(QWidget):
    """自带投影的容器：内容放进它的布局，投影画在外面留出的边距里。

    边距就是羽化半径——所以**它的外框比视觉上的卡片大一圈**，
    叠放/对齐时要按 `pad` 算，不能当普通容器用。

    `radius` 必须与内层卡片的 QSS 圆角一致，否则投影的圆角与卡片的圆角
    对不上，边缘会露出一圈"重影"（投影比卡片圆角小 → 四个角发方）。
    """

    def __init__(self, theme, tier: str = "near", parent: QWidget | None = None,
                 radius: int | None = None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._tier = tier
        self._radius = radius
        self._cache: QPixmap | None = None
        self._key: tuple | None = None
        self.apply_theme(theme)

    def apply_theme(self, theme) -> None:
        self._t = theme
        cfg = P.SHADOW_TIERS[self._tier]
        self._pad = cfg["blur"] + max(abs(cfg["dx"]), abs(cfg["dy"]))
        if self._radius is None:
            self._radius = self._t.radius.lg      # 浮层卡片的圆角（见 QSS）
        self._cache = None          # 颜色变了，缓存作废
        self._key = None
        self.update()

    @property
    def pad(self) -> int:
        """投影占掉的边距。外面要按它对齐。"""
        return self._pad

    def paintEvent(self, _):                            # noqa: N802
        key = (self.width(), self.height(), int(self.devicePixelRatioF() * 100),
               self._t.name, self._t.p.shadow_near, self._t.p.shadow_far)
        if key != self._key:
            color = getattr(self._t.p, "shadow_" + self._tier)
            inner_w = max(1, self.width() - self._pad * 2)
            inner_h = max(1, self.height() - self._pad * 2)
            self._cache = render_shadow(inner_w, inner_h, self._radius,
                                        self._tier, color, self.devicePixelRatioF())
            self._key = key
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
