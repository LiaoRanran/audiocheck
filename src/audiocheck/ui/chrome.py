"""无边框窗口外壳：自绘标题栏 + 系统级窗口操作。

为什么要自绘：原生标题栏在深色主题下会露出一条灰白，是"业余感"最大的来源。

窗口移动/缩放用 Qt 的 `QWindow.startSystemMove/startSystemResize` 交给系统处理，
而不是自己算坐标——后者在多显示器 + 不同 DPI 下必然出错。
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

GRAB = 6  # 边缘可拖拽区域宽度（px）


class EdgeGrip(QWidget):
    """窗口边缘的透明拖拽条。按下时把缩放交给系统。"""

    def __init__(self, parent: QWidget, edge: Qt.Edge):
        super().__init__(parent)
        self._edge = edge
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        cursor = {
            Qt.LeftEdge: Qt.SizeHorCursor, Qt.RightEdge: Qt.SizeHorCursor,
            Qt.TopEdge: Qt.SizeVerCursor, Qt.BottomEdge: Qt.SizeVerCursor,
            Qt.TopEdge | Qt.LeftEdge: Qt.SizeFDiagCursor,
            Qt.BottomEdge | Qt.RightEdge: Qt.SizeFDiagCursor,
            Qt.TopEdge | Qt.RightEdge: Qt.SizeBDiagCursor,
            Qt.BottomEdge | Qt.LeftEdge: Qt.SizeBDiagCursor,
        }.get(edge, Qt.ArrowCursor)
        self.setCursor(cursor)
        self.raise_()

    def mousePressEvent(self, e):                       # noqa: N802
        if e.button() == Qt.LeftButton:
            wh = self.window().windowHandle()
            if wh is not None:
                wh.startSystemResize(self._edge)
        super().mousePressEvent(e)


class TitleBar(QFrame):
    """自绘标题栏：图标 + 标题 + 副标题 + 窗口按钮。"""

    def __init__(self, parent: QWidget, title: str = "", subtitle: str = "", icon: QIcon | None = None):
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(44)
        self._drag_active = False

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 0, 8, 0)
        lay.setSpacing(8)

        self.icon_lbl = QLabel()
        if icon is not None and not icon.isNull():
            self.icon_lbl.setPixmap(icon.pixmap(18, 18))
        lay.addWidget(self.icon_lbl)

        self.title = QLabel(title)
        self.title.setObjectName("TitleText")
        lay.addWidget(self.title)
        self.subtitle = QLabel(subtitle)
        self.subtitle.setObjectName("BrandSub")
        lay.addWidget(self.subtitle)
        lay.addStretch(1)

        self.btn_min = self._btn("min")
        self.btn_max = self._btn("max")
        self.btn_close = self._btn("close")
        for b in (self.btn_min, self.btn_max, self.btn_close):
            lay.addWidget(b)

        self.btn_min.clicked.connect(lambda: self.window().showMinimized())
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(lambda: self.window().close())

    def _btn(self, kind: str) -> QToolButton:
        b = QToolButton(self)
        b.setObjectName("WinBtn")
        b.setProperty("kind", kind)
        # 32×32：标题栏 44（=4×11），按钮 32（=4×8），上下各留 6px 做视觉居中。
        # 原值 34×30 两者都脱离 4px 栅格，且 30px 高在 44px 栏里偏小。
        # 32 同时是通用最小点击目标尺寸。
        b.setFixedSize(32, 32)
        b.setCursor(Qt.ArrowCursor)
        b._kind = kind                            # noqa: SLF001
        b.paintEvent = lambda ev, _b=b: self._paint_btn(_b, ev)
        return b

    def _paint_btn(self, btn: QToolButton, _ev):
        """手绘窗口按钮图形（最小化/最大化/关闭各一条线，不用字体符号）。"""
        pm = QPixmap(btn.size())
        pm.fill(Qt.transparent)
        pr = QPainter(pm)
        pr.setRenderHint(QPainter.Antialiasing, True)
        w, h = pm.width(), pm.height()
        hover = btn.underMouse()
        pen = QPen(QColor("#e8ecf3" if hover else "#98a2b3"))
        pen.setWidthF(1.3)
        pen.setCapStyle(Qt.RoundCap)
        pr.setPen(pen)
        cx, cy = w / 2.0, h / 2.0
        kind = getattr(btn, "_kind", "min")
        if kind == "min":
            pr.drawLine(QPoint(cx - 5, cy + 3), QPoint(cx + 5, cy + 3))
        elif kind == "max":
            pr.drawRect(QRect(int(cx - 4.5), int(cy - 3.5), 9, 7))
        else:
            pr.drawLine(QPoint(cx - 4.5, cy - 4.5), QPoint(cx + 4.5, cy + 4.5))
            pr.drawLine(QPoint(cx + 4.5, cy - 4.5), QPoint(cx - 4.5, cy + 4.5))
        pr.end()
        p = QPainter(btn)
        p.drawPixmap(0, 0, pm)

    def _toggle_max(self):
        w = self.window()
        if w.isMaximized():
            w.showNormal()
        else:
            w.showMaximized()

    def mousePressEvent(self, e):                       # noqa: N802
        if e.button() == Qt.LeftButton:
            wh = self.window().windowHandle()
            if wh is not None:
                wh.startSystemMove()
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):                 # noqa: N802
        if e.button() == Qt.LeftButton:
            self._toggle_max()
        super().mouseDoubleClickEvent(e)


class FramelessWindow(QMainWindow):
    """无边框主窗口。内容区由子类填充；边缘 8 条拖拽区自动布局。"""

    windowStateChanged = Signal(bool)

    def __init__(self, title: str = "", subtitle: str = "", icon: QIcon | None = None):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.setAttribute(Qt.WA_TranslucentBackground, False)
        self._title_text = title
        self._subtitle = subtitle
        self._icon = icon

        root = QWidget()
        root.setObjectName("Root")
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.titlebar = TitleBar(root, title, subtitle, icon)
        v.addWidget(self.titlebar)
        self.content_host = QWidget(root)
        self.content_host.setObjectName("ContentHost")
        v.addWidget(self.content_host, 1)
        self.setCentralWidget(root)

        self._grips = [EdgeGrip(root, e) for e in (
            Qt.LeftEdge, Qt.RightEdge, Qt.TopEdge, Qt.BottomEdge,
            Qt.TopEdge | Qt.LeftEdge, Qt.TopEdge | Qt.RightEdge,
            Qt.BottomEdge | Qt.LeftEdge, Qt.BottomEdge | Qt.RightEdge)]

    def set_title(self, text: str) -> None:
        self.titlebar.title.setText(text)

    def set_subtitle(self, text: str) -> None:
        self.titlebar.subtitle.setText(text)

    def content(self) -> QWidget:
        """内容宿主容器，子类往里加布局。"""
        return self.content_host

    def resizeEvent(self, e):                           # noqa: N802
        super().resizeEvent(e)
        self._layout_grips()

    def changeEvent(self, e):                          # noqa: N802
        super().changeEvent(e)
        if e.type() == QEvent.WindowStateChange:
            self.windowStateChanged.emit(self.isMaximized())
            self._layout_grips()

    def _layout_grips(self) -> None:
        r: QRect = self.centralWidget().rect()
        g = GRAB
        place = {
            Qt.LeftEdge: QRect(0, g, g, r.height() - 2 * g),
            Qt.RightEdge: QRect(r.width() - g, g, g, r.height() - 2 * g),
            Qt.TopEdge: QRect(g, 0, r.width() - 2 * g, g),
            Qt.BottomEdge: QRect(g, r.height() - g, r.width() - 2 * g, g),
            Qt.TopEdge | Qt.LeftEdge: QRect(0, 0, g + 4, g + 4),
            Qt.TopEdge | Qt.RightEdge: QRect(r.width() - g - 4, 0, g + 4, g + 4),
            Qt.BottomEdge | Qt.LeftEdge: QRect(0, r.height() - g - 4, g + 4, g + 4),
            Qt.BottomEdge | Qt.RightEdge: QRect(r.width() - g - 4, r.height() - g - 4, g + 4, g + 4),
        }
        for grip in self._grips:
            rect = place.get(grip._edge)               # noqa: SLF001
            if rect is not None:
                grip.setGeometry(rect)
                grip.setVisible(not self.isMaximized())
