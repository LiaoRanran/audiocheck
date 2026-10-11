#!/usr/bin/env python3
"""audiocheck 主题层：配色系统、QSS 生成、矢量图标（全部手绘，不依赖外部资源）。

设计约束：
  · 所有颜色只从这里取，避免散落的硬编码色值导致主题切换时漏改
  · 图标用 QPainterPath 矢量绘制，2x 渲染 + devicePixelRatio，任意 DPI 都锐利
"""
import os

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (QColor, QFontDatabase, QIcon, QPainter, QPainterPath, QPen,
                           QPixmap, QPolygonF)

# --------------------------------------------------------------------- 配色

DARK = {
    "name": "dark",
    "bg": "#0d0f14",
    "surface": "#151922",
    "surface2": "#1b202b",
    "surface3": "#212836",
    "border": "#252c39",
    "border_hi": "#333c4d",
    "text": "#e8ecf3",
    "text_dim": "#8b94a8",
    "text_faint": "#5a6377",
    "accent": "#3d7dff",
    "accent_hi": "#5a92ff",
    "accent_soft": "#16243d",
    "ok": "#35c46a",
    "warn": "#e0a92e",
    "bad": "#f0524a",
    "ok_bg": "#0b1f13",
    "warn_bg": "#221a09",
    "bad_bg": "#281110",
    "ok_border": "#1d5a33",
    "warn_border": "#5e4818",
    "bad_border": "#6b2521",
    "grid": "#232a37",
    "plot_bg": "#0b0e13",
    "selection": "#1a2740",
}

LIGHT = {
    "name": "light",
    "bg": "#f2f4f9",
    "surface": "#ffffff",
    "surface2": "#f7f9fc",
    "surface3": "#eef1f7",
    "border": "#dde3ee",
    "border_hi": "#c4cddd",
    "text": "#161c2b",
    "text_dim": "#5c6579",
    "text_faint": "#8a94a8",
    "accent": "#2563eb",
    "accent_hi": "#1d4fd8",
    "accent_soft": "#e3ecff",
    "ok": "#15a34a",
    "warn": "#c07d05",
    "bad": "#dc2626",
    "ok_bg": "#e9f9ef",
    "warn_bg": "#fdf4e3",
    "bad_bg": "#fdeceb",
    "ok_border": "#b6e6c8",
    "warn_border": "#f0dcae",
    "bad_border": "#f5c6c2",
    "grid": "#e6ebf3",
    "plot_bg": "#ffffff",
    "selection": "#dbe7ff",
}

THEMES = {"dark": DARK, "light": LIGHT}

LEVEL_KEY = {"ok": "ok", "warn": "warn", "bad": "bad"}
LEVEL_CN = {"ok": "合格", "warn": "有疑", "bad": "不达标"}
LEVEL_COLOR_KEY = {"ok": "ok", "warn": "warn", "bad": "bad"}


def qss(p):
    """由配色字典生成完整 QSS。"""
    return """
* {{ outline: 0; }}
QWidget {{ background:{bg}; color:{text}; font-size:13px;
           font-family:"Microsoft YaHei UI","Microsoft YaHei","Segoe UI",sans-serif; }}
QMainWindow, QDialog {{ background:{bg}; }}
QToolTip {{ background:{surface3}; color:{text}; border:1px solid {border_hi};
            padding:5px 8px; border-radius:6px; }}

/* ---------- 顶栏 ---------- */
#HeaderBar {{ background:{surface}; border-bottom:1px solid {border}; }}
#AppTitle {{ font-size:15px; font-weight:700; letter-spacing:.3px; }}
#AppSub {{ color:{text_faint}; font-size:11.5px; }}
#HeaderMeta {{ color:{text_dim}; font-size:11.5px; }}

/* ---------- 工具条 ---------- */
#Toolbar {{ background:{surface}; border-bottom:1px solid {border}; }}
#Toolbar QToolButton {{ background:transparent; border:1px solid transparent; border-radius:7px;
                        padding:6px 11px; color:{text}; }}
#Toolbar QToolButton:hover {{ background:{surface3}; border-color:{border_hi}; }}
#Toolbar QToolButton:pressed {{ background:{surface2}; }}
#Toolbar QToolButton:disabled {{ color:{text_faint}; }}
#Toolbar QToolButton#Primary {{ background:{accent}; border-color:{accent}; color:#fff; font-weight:600; }}
#Toolbar QToolButton#Primary:hover {{ background:{accent_hi}; border-color:{accent_hi}; }}
#Toolbar QToolButton#Primary:disabled {{ background:{surface3}; border-color:{border};
                                         color:{text_faint}; }}
QFrame#Sep {{ background:{border}; max-width:1px; }}

/* ---------- 输入 ---------- */
QLineEdit, QComboBox, QSpinBox {{ background:{surface2}; border:1px solid {border};
    border-radius:7px; padding:5px 9px; selection-background-color:{accent}; color:{text}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color:{accent}; }}
QComboBox::drop-down {{ border:0; width:20px; }}
QComboBox QAbstractItemView {{ background:{surface2}; border:1px solid {border_hi};
    selection-background-color:{accent}; color:{text}; padding:3px; }}
QCheckBox {{ spacing:7px; }}
QCheckBox::indicator {{ width:15px; height:15px; border-radius:4px;
    border:1px solid {border_hi}; background:{surface2}; }}
QCheckBox::indicator:checked {{ background:{accent}; border-color:{accent}; }}

/* ---------- 表格 ---------- */
QTableWidget {{ background:{surface}; alternate-background-color:{surface2};
    gridline-color:transparent; border:1px solid {border}; border-radius:9px;
    selection-background-color:{selection}; selection-color:{text}; }}
QTableWidget::item {{ padding:6px 8px; border:0; }}
QHeaderView {{ background:transparent; }}
QHeaderView::section {{ background:{surface2}; color:{text_dim}; border:0;
    border-bottom:1px solid {border}; border-right:1px solid {border};
    padding:8px 9px; font-weight:600; font-size:11.5px; }}
QHeaderView::section:last {{ border-right:0; }}
QTableCornerButton::section {{ background:{surface2}; border:0; }}

/* ---------- 容器 ---------- */
QFrame#Card, QWidget#Card {{ background:{surface}; border:1px solid {border}; border-radius:10px; }}
QWidget#Metric {{ background:{surface2}; border:1px solid {border}; border-radius:8px; }}
QLabel#MetricK {{ color:{text_faint}; font-size:10.5px; }}
QLabel#MetricV {{ font-size:12.5px; font-weight:600; }}
#Banner {{ border-radius:10px; padding:13px 16px; font-size:14px; font-weight:600; }}
#HeroNum {{ font-size:27px; font-weight:700; }}
#HeroK {{ color:{text_faint}; font-size:11px; }}
#SectionTitle {{ color:{text_dim}; font-size:11.5px; font-weight:600; letter-spacing:.6px; }}
#Advice {{ background:{surface2}; border:1px solid {border}; border-radius:9px;
           padding:10px 12px; color:{text}; font-size:12.5px; }}
#Empty {{ color:{text_faint}; font-size:13px; }}

/* ---------- 标签页 ---------- */
QTabWidget::pane {{ border:1px solid {border}; border-radius:9px; top:-1px; background:{surface}; }}
QTabBar::tab {{ background:transparent; color:{text_dim}; padding:7px 15px; border:0;
    border-bottom:2px solid transparent; margin-right:2px; font-size:12.5px; }}
QTabBar::tab:hover {{ color:{text}; }}
QTabBar::tab:selected {{ color:{text}; border-bottom-color:{accent}; font-weight:600; }}

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{border_hi}; border-radius:5px; min-height:30px; }}
QScrollBar::handle:vertical:hover {{ background:{text_faint}; }}
QScrollBar:horizontal {{ background:transparent; height:10px; margin:2px; }}
QScrollBar::handle:horizontal {{ background:{border_hi}; border-radius:5px; min-width:30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}

/* ---------- 状态栏 ---------- */
QStatusBar {{ background:{surface}; border-top:1px solid {border}; color:{text_dim}; }}
QStatusBar::item {{ border:0; }}
QProgressBar {{ background:{surface3}; border:0; border-radius:3px; height:5px;
                text-align:center; color:transparent; }}
QProgressBar::chunk {{ background:{accent}; border-radius:3px; }}

/* ---------- 其它 ---------- */
QSplitter::handle {{ background:transparent; }}
QSplitter::handle:horizontal {{ width:6px; }}
QSplitter::handle:hover {{ background:{accent_soft}; }}
QMenu {{ background:{surface2}; border:1px solid {border_hi}; border-radius:8px; padding:5px; }}
QMenu::item {{ padding:6px 22px 6px 12px; border-radius:6px; }}
QMenu::item:selected {{ background:{accent}; color:#fff; }}
QMenu::separator {{ height:1px; background:{border}; margin:4px 8px; }}
QMenuBar {{ background:{surface}; }}
QMenuBar::item {{ padding:5px 10px; border-radius:6px; }}
QMenuBar::item:selected {{ background:{surface3}; }}
QPushButton {{ background:{surface3}; border:1px solid {border_hi}; border-radius:7px;
    padding:6px 14px; color:{text}; }}
QPushButton:hover {{ border-color:{accent}; }}
QPushButton#Primary {{ background:{accent}; border-color:{accent}; color:#fff; font-weight:600; }}
QPushButton#Primary:hover {{ background:{accent_hi}; }}
QLabel#Drop {{ color:{text_faint}; border:1.5px dashed {border_hi}; border-radius:10px;
    padding:9px; font-size:12px; }}
QLabel#DropHot {{ color:{accent}; border:1.5px dashed {accent}; border-radius:10px;
    padding:9px; font-size:12px; background:{accent_soft}; }}
""".format(**p)


# --------------------------------------------------------------------- 字体

def load_cjk_fonts():
    """Qt6 不再自带字体，缺中文字体会整屏方框——主动注册系统字体。"""
    cands = [
        r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\Deng.ttf",
        r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    fams = []
    for p in cands:
        if os.path.exists(p):
            i = QFontDatabase.addApplicationFont(p)
            if i >= 0:
                fams += QFontDatabase.applicationFontFamilies(i)
    return fams


def pick_family(fams, prefer=("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                              "Noto Sans CJK SC", "Meiryo")):
    for want in prefer:
        for f in fams:
            if f.lower().startswith(want.lower()):
                return f
    return fams[0] if fams else "Sans Serif"


def mpl_font(fams):
    """给 matplotlib 也挑一个能显示中文的字体族。"""
    import matplotlib
    for want in ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Meiryo", "SimHei"):
        for f in fams + [x.name for x in matplotlib.font_manager.fontManager.ttflist]:
            if want.lower() in f.lower():
                return want
    return None


# --------------------------------------------------------------------- 矢量图标

def _painter(size):
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.Antialiasing, True)
    return pm, pr


def make_pixmap(name, color, size=18, weight=1.6):
    """手绘矢量图标：统一 linear 线帽、round 连接，风格一致。"""
    pm, pr = _painter(size)
    s = size
    pen = QPen(QColor(color))
    pen.setWidthF(weight)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    pr.setPen(pen)
    pr.setBrush(Qt.NoBrush)
    m = s * 0.22                                     # 边距

    if name == "add":                                # ＋
        pr.drawLine(QPointF(s / 2, m), QPointF(s / 2, s - m))
        pr.drawLine(QPointF(m, s / 2), QPointF(s - m, s / 2))
    elif name == "folder":                           # 文件夹
        p = QPainterPath()
        p.moveTo(m, s - m)
        p.lineTo(m, m + s * 0.14)
        p.lineTo(s * 0.42, m + s * 0.14)
        p.lineTo(s * 0.52, m + s * 0.30)
        p.lineTo(s - m, m + s * 0.30)
        p.lineTo(s - m, s - m)
        p.closeSubpath()
        pr.drawPath(p)
    elif name == "play":                             # ▶
        p = QPainterPath()
        p.moveTo(m + s * 0.06, m)
        p.lineTo(s - m, s / 2)
        p.lineTo(m + s * 0.06, s - m)
        p.closeSubpath()
        pr.setBrush(QColor(color))
        pr.drawPath(p)
    elif name == "stop":                             # ■
        r = s * 0.26
        pr.setBrush(QColor(color))
        pr.drawRoundedRect(QRectF(r, r, s - 2 * r, s - 2 * r), 2.2, 2.2)
    elif name == "export":                           # 下载/导出
        pr.drawLine(QPointF(s / 2, m), QPointF(s / 2, s * 0.64))
        pr.drawPolyline(QPolygonF([QPointF(s * 0.31, s * 0.46), QPointF(s / 2, s * 0.66),
                                   QPointF(s * 0.69, s * 0.46)]))
        pr.drawLine(QPointF(m, s - m), QPointF(s - m, s - m))
    elif name == "trash":                            # 清空
        pr.drawLine(QPointF(m, m + s * 0.16), QPointF(s - m, m + s * 0.16))
        pr.drawLine(QPointF(s * 0.40, m + s * 0.16), QPointF(s * 0.40, m + s * 0.05))
        pr.drawLine(QPointF(s * 0.60, m + s * 0.16), QPointF(s * 0.60, m + s * 0.05))
        p = QPainterPath()
        p.moveTo(m + s * 0.07, m + s * 0.16)
        p.lineTo(m + s * 0.12, s - m)
        p.lineTo(s - m - s * 0.12, s - m)
        p.lineTo(s - m - s * 0.07, m + s * 0.16)
        pr.drawPath(p)
    elif name == "search":                           # 放大镜
        pr.drawEllipse(QPointF(s * 0.44, s * 0.44), s * 0.24, s * 0.24)
        pr.drawLine(QPointF(s * 0.62, s * 0.62), QPointF(s * 0.83, s * 0.83))
    elif name == "gear":                             # 齿轮
        pr.drawEllipse(QPointF(s / 2, s / 2), s * 0.17, s * 0.17)
        import math
        for i in range(8):
            a = i * math.pi / 4
            x1, y1 = s / 2 + math.cos(a) * s * 0.26, s / 2 + math.sin(a) * s * 0.26
            x2, y2 = s / 2 + math.cos(a) * s * 0.36, s / 2 + math.sin(a) * s * 0.36
            pr.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif name == "sun":
        pr.drawEllipse(QPointF(s / 2, s / 2), s * 0.16, s * 0.16)
        import math
        for i in range(8):
            a = i * math.pi / 4
            x1, y1 = s / 2 + math.cos(a) * s * 0.25, s / 2 + math.sin(a) * s * 0.25
            x2, y2 = s / 2 + math.cos(a) * s * 0.36, s / 2 + math.sin(a) * s * 0.36
            pr.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    elif name == "moon":
        p = QPainterPath()
        p.moveTo(s * 0.66, m)
        p.arcTo(QRectF(m, m, s - 2 * m, s - 2 * m), 70, 220)
        p.closeSubpath()
        pr.setBrush(QColor(color))
        pr.setPen(Qt.NoPen)
        pr.drawPath(p)
    elif name == "chart":                            # 迷你柱状
        for i, h in enumerate((0.30, 0.55, 0.42, 0.72)):
            x = m + i * (s - 2 * m) / 4
            w = (s - 2 * m) / 7
            pr.setBrush(QColor(color))
            pr.setPen(Qt.NoPen)
            pr.drawRoundedRect(QRectF(x, s - m - (s - 2 * m) * h, w, (s - 2 * m) * h), 1.6, 1.6)
    elif name == "layers":                           # 对比：两条叠加曲线
        pr.drawLine(QPointF(m, s * 0.62), QPointF(s - m, s * 0.62))
        p = QPainterPath()
        p.moveTo(m, s * 0.62)
        p.cubicTo(s * 0.36, s * 0.20, s * 0.62, s * 0.86, s - m, s * 0.38)
        pr.drawPath(p)
    elif name == "wave":                             # 波形
        p = QPainterPath()
        p.moveTo(m, s / 2)
        for k in range(10):
            x = m + (s - 2 * m) * k / 9
            y = s / 2 + (s * 0.30 if k % 2 else -s * 0.30)
            p.lineTo(x, y)
        pr.drawPath(p)
    elif name == "grid":                             # 频段格
        for i in range(3):
            for j in range(3):
                pr.setBrush(QColor(color))
                pr.setPen(Qt.NoPen)
                pr.drawRect(QRectF(m + i * (s - 2 * m) / 3 + 1, m + j * (s - 2 * m) / 3 + 1,
                                   (s - 2 * m) / 3 - 1.6, (s - 2 * m) / 3 - 1.6))
    elif name == "reload":                           # 重新分析
        pr.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 40 * 16, 280 * 16)
        pr.drawPolyline(QPolygonF([QPointF(s - m - s * 0.16, m + s * 0.02),
                                   QPointF(s - m, m + s * 0.16),
                                   QPointF(s - m - s * 0.20, m + s * 0.26)]))
    elif name == "copy":
        pr.drawRoundedRect(QRectF(m, m + s * 0.12, s * 0.5, s * 0.58), 2.4, 2.4)
        pr.drawRoundedRect(QRectF(m + s * 0.20, m, s * 0.5, s * 0.58), 2.4, 2.4)
    pr.end()
    return pm


def icon(name, color="#e8ecf3", size=18):
    ic = QIcon()
    ic.addPixmap(make_pixmap(name, color, size * 2, 1.9), QIcon.Normal, QIcon.Off)  # 2x 更锐
    ic.addPixmap(make_pixmap(name, color, size * 2, 1.9), QIcon.Disabled, QIcon.Off)
    return ic


def app_icon(p):
    """程序图标：一块「频谱砖墙」——低区密、高区骤停。"""
    pm = QPixmap(128, 128)
    pm.fill(Qt.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.Antialiasing, True)
    pr.setPen(Qt.NoPen)
    pr.setBrush(QColor(p["surface3"]))
    pr.drawRoundedRect(QRectF(4, 4, 120, 120), 26, 26)
    heights = [30, 52, 74, 62, 84, 70, 88, 66, 78, 40, 16, 6]
    for i, h in enumerate(heights):
        x = 18 + i * 7.8
        col = QColor(p["accent"]) if i < 9 else QColor(p["bad"])
        pr.setBrush(col)
        pr.drawRoundedRect(QRectF(x, 112 - h, 5.4, h), 2.4, 2.4)
    pr.end()
    return QIcon(pm)
