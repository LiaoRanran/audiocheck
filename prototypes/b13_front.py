"""B13 前端雏形：完整外壳（导航真图标 + 主区 + 交互态），按已判语法一把拼齐。

一网打尽的最小可见面：边框(5px+双线) / 图标(矢量 rail) / 字体排版(Plex+思源+分辨率)
/ 交互(hover·active) / 语法合一(G1 氛围 + 深度三层 + detailsBg + 数据纯净)。
渲染：系统 python + offscreen + 字体捆绑断言。状态由参数注出（不靠真实鼠标）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _b12_fonts  # noqa: E402
import _kit as K  # noqa: E402
from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from audiocheck.ui import palette as P  # noqa: E402

THEME = os.environ.get("B13_THEME", "light")
KIND = os.environ.get("B12_KIND", "g1")
# 暗色：整体换 PAL（K.band / K.on_tint 都读 K.PAL）
if THEME == "dark":
    from audiocheck.ui import theme as _T
    K.PAL = P.build(_T.BUILTIN_SEED, "dark", "calm")
PAL = K.PAL
UI, MONO, CJK = _b12_fonts.UI, _b12_fonts.MONO, _b12_fonts.CJK
SHOTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "shots", "b13")

# ---- 字号 = f(分辨率档)（D4，HD_QHD_4K 思想）；档位由环境选 ------------------
# 证据图统一窗口尺寸，档位只改字号——这样三档是同版面的**字号梯度**对照。
TIER = os.environ.get("B13_TIER", "HD")
W, H = 1400, 880
ROLES = {"rail": 9, "label": 10, "body": 12, "title": 14, "num": 16,
         "verdict": 22, "hero": 40}


def fs(role):
    px = ROLES[role]
    return px * {"HD": 1.0, "QHD": 1.17, "4K": 1.33}[TIER]


def F(role, weight=400, num=False) -> QFont:
    f = QFont()
    f.setFamilies([MONO] if num else [UI, CJK])
    f.setPointSizeF(fs(role))
    f.setWeight(QFont.Weight.DemiBold if weight >= 600
                else QFont.Weight.Medium if weight >= 500 else QFont.Weight.Normal)
    return f


# 让 _kit 内部（顶栏/曲线注记）也用捆绑字体
def _patch(f, size, weight=400, num=False):
    return F("body" if False else "title", weight=weight, num=num) if False else \
        _mk(size, weight, num)


def _mk(size, weight, num):
    f = QFont()
    f.setFamilies([MONO] if num else [UI, CJK])
    f.setPointSizeF(size)
    f.setWeight(QFont.Weight.DemiBold if weight >= 600 else QFont.Weight.Normal)
    return f


K.tune_font = _mk
K.num_font = lambda size: _mk(size, 400, True)


def lbl(text, role, weight=400, num=False, color=None) -> QLabel:
    w = QLabel(text)
    w.setFont(F(role, weight=weight, num=num))
    if color:
        w.setStyleSheet("color:%s;" % color)
    return w


def veil_alpha(u, v):
    base_L = P.srgb_to_oklch(K._atmo_hex(KIND, u, v))[0]
    return min(0.93, max(0.76, 0.86 + 0.30 * (0.55 - base_L)))   # 光随底色（D-light）


def _rgba(hexc, a):
    h = hexc.lstrip("#")
    return "rgba(%d,%d,%d,%d)" % (int(h[0:2], 16), int(h[2:4], 16),
                                  int(h[4:6], 16), int(a * 255))


def panel_style(u, v, a=None, radius=5, hi=True):
    """GR 深度：veil + 5px 圆角 + 1px 外暗 + 内高光线。"""
    a = veil_alpha(u, v) if a is None else a
    return ("background:%s;border-radius:%dpx;border:1px solid %s;%s"
            % (K.veil(KIND, u, v, a, light=(THEME == "light")), radius, PAL["border"],
               ("border-top:1px solid %s;" % _rgba("#ffffff", 0.5)) if hi else ""))


def apply_bg(w, style):
    """QWidget 的 setStyleSheet 背景**必须**挂 `WA_StyledBackground` 才会绘制，
    否则面板透明、氛围直接透上来（`_kit.dress` 在 B10 记过同一坑）。
    这是 b12/b13 面板"看着半透"的真因——判据只做解析式预测会被它骗。"""
    w.setStyleSheet(style)
    w.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)


def add_shadow(w, blur=18, dy=3, alpha=150):
    fx = QGraphicsDropShadowEffect(w)
    fx.setBlurRadius(blur)
    fx.setOffset(0, dy)
    fx.setColor(QColor(0, 0, 0, alpha))
    w.setGraphicsEffect(fx)


def hair() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background:%s;border:0;" % PAL["border"])
    return f


# ---- 矢量图标（QPainter，仪器语言：细线方角）--------------------------------
class Icon(QWidget):
    NAMES = ("wave", "list", "disc", "gear")

    def __init__(self, name, color, px=22):
        super().__init__()
        self.name, self.color = name, color
        self.setFixedSize(px, px)

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(self.color))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        r = self.rect()
        cx, cy = r.center().x(), r.center().y()
        if self.name == "wave":
            for i, hh in enumerate((0.45, 0.75, 1.0, 0.6, 0.85, 0.5, 0.32)):
                x = cx + (i - 3) * 3.4
                p.drawLine(QPointF(x, cy - 8 * hh), QPointF(x, cy + 8 * hh))
        elif self.name == "list":
            for i in range(3):
                y = cy - 6 + i * 6
                p.setBrush(QColor(self.color))
                p.drawEllipse(QRectF(cx - 8, y - 1.1, 2.2, 2.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawLine(QPointF(cx - 4, y), QPointF(cx + 8, y))
        elif self.name == "disc":
            p.drawEllipse(QRectF(cx - 8, cy - 8, 16, 16))
            p.setBrush(QColor(self.color))
            p.drawEllipse(QRectF(cx - 1.6, cy - 1.6, 3.2, 3.2))
        elif self.name == "gear":
            for i in range(3):
                y = cy - 6 + i * 6
                p.drawLine(QPointF(cx - 8, y), QPointF(cx + 8, y))
                kx = cx - 8 + (i * 8) % 16
                p.setBrush(QColor(self.color))
                p.drawEllipse(QRectF(kx - 1.8, y - 1.8, 3.6, 3.6))
                p.setBrush(Qt.BrushStyle.NoBrush)
        p.end()


class RailItem(QWidget):
    """导航项：矢量图标 + active 左指示条 + hover 底。"""

    def __init__(self, name, index, active=False, hovered=False):
        super().__init__()
        self.index = index
        self.active, self.hovered = active, hovered
        self.setFixedSize(56, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        lay.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        color = PAL["primary"] if active else (PAL["text"] if hovered else PAL["text_dim"])
        lay.addWidget(Icon(name, color, 22), 0, Qt.AlignmentFlag.AlignHCenter)

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.active or self.hovered:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(PAL["primary"] if self.active else PAL["border_hi"]))
            if self.active:
                p.drawRoundedRect(QRectF(0, 12, 3, 24), 1.5, 1.5)      # 左指示条
                p.fillRect(self.rect(), QColor(_rgba(PAL["primary"], 0.10)))  # tint 底
            else:
                p.fillRect(self.rect(), QColor(_rgba(PAL["primary"], 0.05)))  # hover 底
            p.end()


def action_btn(text, hovered=False, primary=True):
    b = QPushButton(text)
    b.setFont(F("label", weight=500))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if primary:
        b.setStyleSheet("QPushButton{background:%s;color:%s;border:1px solid %s;"
                        "border-radius:5px;padding:6px 14px;}"
                        "QPushButton:hover{background:%s;}"
                        % (PAL["primary"], "#ffffff", PAL["primary"],
                           _rgba(PAL["primary"], 0.85)))
    else:
        b.setStyleSheet("QPushButton{background:transparent;color:%s;border:1px solid %s;"
                        "border-radius:5px;padding:6px 14px;}"
                        "QPushButton:hover{background:%s;}"
                        % (PAL["text"], PAL["border_hi"],
                           _rgba(PAL["primary"], 0.08 if not hovered else 0.16)))
    return b


def topbar(case):
    w = QFrame()
    h = QHBoxLayout(w)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(12)
    h.addWidget(lbl("audiocheck", "title", weight=600))
    chip = QLabel("● " + case["verdict"])
    chip.setFont(F("label", weight=600))
    chip.setStyleSheet("color:%s;" % K.band(case["level"]))
    h.addWidget(chip)
    h.addWidget(lbl(case["file"], "label", color=PAL["text_dim"]))
    h.addStretch(1)
    hero = lbl("%.2f" % case["cutoff_khz"], "hero", weight=600, num=True)
    h.addWidget(hero)
    h.addWidget(lbl("kHz", "label", color=PAL["text_dim"]))
    return w


def mk_shell(case, *, nav_active=0, hover_nav=None, hover_btn=False):
    inner = QWidget()
    root_h = QHBoxLayout(inner)
    root_h.setContentsMargins(0, 0, 0, 0)
    root_h.setSpacing(0)

    # ---- 左：图标导航栏（真矢量 + active/hover 态）----
    rail = QFrame()
    rail.setFixedWidth(56)
    rail.setStyleSheet("background:%s;border:0;border-right:1px solid %s;"
                       % (PAL["surface"], PAL["border"]))
    rv = QVBoxLayout(rail)
    rv.setContentsMargins(0, 12, 0, 12)
    rv.setSpacing(4)
    for i, name in enumerate(Icon.NAMES):
        rv.addWidget(RailItem(name, i, active=(i == nav_active),
                              hovered=(i == hover_nav)), 0,
                     Qt.AlignmentFlag.AlignHCenter)
    rv.addStretch(1)
    root_h.addWidget(rail)

    # ---- 右：主区 ----
    main = QWidget()
    mv = QVBoxLayout(main)
    mv.setContentsMargins(28, 22, 28, 22)
    mv.setSpacing(16)

    tb = topbar(case)
    apply_bg(tb, panel_style(0.55, 0.08))
    add_shadow(tb)
    mv.addWidget(tb)

    mv.addWidget(K.verdict_band(case))

    vr = QHBoxLayout()
    vr.addWidget(lbl("● " + case["verdict"], "verdict", weight=600, color=K.on_tint(case["level"])))
    vr.addStretch(1)
    vr.addWidget(lbl("真实截止", "label", color=PAL["text_dim"]))
    vr.addWidget(lbl("%.2f kHz" % case["cutoff_khz"], "num", weight=600, num=True))
    mv.addLayout(vr)
    mv.addWidget(lbl(case["why"], "body", color=PAL["text_dim"]))

    # 曲线面板（数据区纯净）
    pw = QFrame()
    pv = QVBoxLayout(pw)
    pv.setContentsMargins(4, 4, 4, 4)
    apply_bg(pw, panel_style(0.5, 0.5))
    add_shadow(pw, blur=22, dy=3)
    pw.layout().addWidget(K.PlotWidget.make(case))
    mv.addWidget(pw, 5)
    mv.addWidget(hair())

    # detailsBg 第二底色 + 指标
    dw = QFrame()
    dv = QVBoxLayout(dw)
    dv.setContentsMargins(14, 12, 14, 12)
    dv.setSpacing(10)
    apply_bg(dw, panel_style(0.5, 0.78, a=0.78))
    nums = [("砖墙陡度", "67 dB/oct"), ("集成响度", "-22.9 LUFS"),
            ("真峰值", "+0.5 dBTP"), ("动态范围", "15.4 dB")]
    nr = QHBoxLayout()
    nr.setSpacing(0)
    for i, (k, v) in enumerate(nums):
        c = QVBoxLayout()
        c.setSpacing(2)
        c.addWidget(lbl(k, "label", color=PAL["text_faint"]))
        c.addWidget(lbl(v, "num", weight=600, num=True))
        nr.addLayout(c, 1)
        if i < len(nums) - 1:
            s = QFrame()
            s.setFixedWidth(1)
            s.setStyleSheet("background:%s;border:0;" % PAL["border"])
            nr.addWidget(s)
    dv.addLayout(nr)
    btns = QHBoxLayout()
    btns.addWidget(action_btn("▸ 全部 18 项指标", hovered=hover_btn))
    btns.addWidget(action_btn("导出报告", primary=False))
    btns.addStretch(1)
    dv.addLayout(btns)
    mv.addWidget(dw)

    root_h.addWidget(main, 1)
    root = K.AtmoRoot.wrap(KIND, inner)
    return root


def shot(win, name):
    import perf_probe
    win.resize(W, H)
    win.show()
    perf_probe.pump(K.app(), 0.5)
    os.makedirs(SHOTS, exist_ok=True)
    p = os.path.join(SHOTS, "%s.png" % name)
    win.grab().save(p)
    print("  出图 %-22s（%d KB）" % (os.path.basename(p), os.path.getsize(p) // 1024))
    return p


if __name__ == "__main__":
    K.app()
    _b12_fonts.load()
    case = K.cases()[1]                      # 用户认的 G1·不达标态
    suf = "%s_%s" % (TIER, THEME)
    shot(mk_shell(case, nav_active=0), "shell_%s_default" % suf)
    shot(mk_shell(case, nav_active=1, hover_nav=2, hover_btn=True), "shell_%s_hover" % suf)
    print("前端外壳雏形完成：TIER=%s THEME=%s KIND=%s" % (TIER, THEME, KIND))
