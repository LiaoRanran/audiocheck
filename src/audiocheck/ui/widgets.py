#!/usr/bin/env python3
"""audiocheck 控件层：自绘仪表 + 可复用的键值表。"""
import math

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QHeaderView, QTableWidget, QTableWidgetItem,
                               QWidget)

from . import theme as T


class Gauge(QWidget):
    """半环仪表：显示「真实截止 / 奈奎斯特」占比，颜色随结论变化。"""

    def __init__(self, size=124, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.p = T.DARK
        self.value = None          # 0..1
        self.level = None
        self.main = "—"
        self.sub = "真实截止"
        self.caption = ""

    def set_data(self, ratio, level, main, sub, caption=""):
        self.value = ratio
        self.level = level
        self.main, self.sub, self.caption = main, sub, caption
        self.update()

    def set_theme(self, p):
        self.p = p
        self.update()

    def paintEvent(self, _):
        p = self.p
        pr = QPainter(self)
        pr.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        thick = 9.0
        pad = thick / 2 + 3
        rect = QRectF(pad, pad, w - 2 * pad, h - 2 * pad)
        start, span = 225.0, -270.0                      # 从左上开始，逆时针 270°

        pr.setPen(QPen(QColor(p["surface3"]), thick, Qt.SolidLine, Qt.RoundCap))
        pr.drawArc(rect, int(start * 16), int(span * 16))

        if self.value is not None:
            col = QColor(p.get(self.level or "accent", p["accent"]))
            grad = QConicalGradient(rect.center(), 225)
            grad.setColorAt(0.0, col.lighter(125))
            grad.setColorAt(0.5, col)
            grad.setColorAt(1.0, col.darker(115))
            pr.setPen(QPen(grad, thick, Qt.SolidLine, Qt.RoundCap))
            frac = max(0.0, min(1.0, float(self.value)))
            pr.drawArc(rect, int(start * 16), int(span * frac * 16))

        pr.setPen(QColor(p["text"]))
        f = QFont(self.font())
        f.setPointSizeF(15.5)
        f.setBold(True)
        pr.setFont(f)
        pr.drawText(QRectF(0, h * 0.30, w, h * 0.30), Qt.AlignCenter, self.main)

        f.setPointSizeF(8.4)
        f.setBold(False)
        pr.setFont(f)
        pr.setPen(QColor(p["text_faint"]))
        pr.drawText(QRectF(0, h * 0.55, w, h * 0.18), Qt.AlignCenter, self.sub)

        if self.caption:
            f.setPointSizeF(7.8)
            pr.setFont(f)
            pr.setPen(QColor(p["text_dim"]))
            pr.drawText(QRectF(0, h * 0.70, w, h * 0.20), Qt.AlignCenter, self.caption)
        pr.end()


class BarMeter(QWidget):
    """横向电平条：用于响度/真峰值这类「对照满刻度」的量。"""

    def __init__(self, height=22, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self.p = T.DARK
        self.value = None
        self.lo, self.hi = -30.0, 5.0
        self.label = ""

    def set_data(self, value, lo, hi, label=""):
        self.value, self.lo, self.hi, self.label = value, lo, hi, label
        self.update()

    def set_theme(self, p):
        self.p = p
        self.update()

    def paintEvent(self, _):
        p = self.p
        pr = QPainter(self)
        pr.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        r = QRectF(0, h / 2 - 4, w, 8)
        pr.setPen(Qt.NoPen)
        pr.setBrush(QColor(p["surface3"]))
        pr.drawRoundedRect(r, 4, 4)
        if self.value is None:
            pr.end()
            return
        frac = max(0.0, min(1.0, (self.value - self.lo) / (self.hi - self.lo)))
        col = QColor(p["ok"] if self.value < -1 else (p["warn"] if self.value <= 0 else p["bad"]))
        pr.setBrush(col)
        pr.drawRoundedRect(QRectF(0, h / 2 - 4, max(6.0, w * frac), 8), 4, 4)
        f = QFont(self.font())
        f.setPointSizeF(8.0)
        pr.setFont(f)
        pr.setPen(QColor(p["text_faint"]))
        pr.drawText(QRectF(2, 1, w, h / 2 - 3), Qt.AlignVCenter | Qt.AlignLeft, self.label)
        pr.end()


class KVTable(QTableWidget):
    """两列键值表：只读、可选中复制、带斑马纹与悬浮提示。"""

    def __init__(self, parent=None):
        super().__init__(0, 2, parent)
        self.setHorizontalHeaderLabels(["字段", "值"])
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        h = self.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setHighlightSections(False)
        self.verticalHeader().setDefaultSectionSize(27)
        self._p = T.DARK

    def set_theme(self, p):
        self._p = p

    def set_rows(self, rows, mono_keys=()):
        self.setRowCount(0)
        for k, v in rows:
            i = self.rowCount()
            self.insertRow(i)
            a = QTableWidgetItem(str(k))
            a.setForeground(QColor(self._p["text_dim"]))
            b = QTableWidgetItem(str(v))
            if str(k) in mono_keys:
                f = QFont("Consolas")
                f.setPointSizeF(9.0)
                b.setFont(f)
            b.setToolTip(str(v))
            self.setItem(i, 0, a)
            self.setItem(i, 1, b)

    def fit_rows(self, rows):
        """行数少时把高度收紧，避免大片空白。"""
        self.set_rows(rows)
        h = self.verticalHeader().defaultSectionSize() * max(1, len(rows)) + 30
        self.setMinimumHeight(min(h, 520))


def fmt_num(v, unit="", digits=1, signed=False):
    if v is None:
        return "—"
    try:
        s = ("%+.*f" if signed else "%.*f") % (digits, float(v))
    except (TypeError, ValueError):
        return str(v)
    return "%s%s" % (s, unit)


def ratio_caption(x):
    try:
        return "%.0f%%" % (float(x) * 100)
    except (TypeError, ValueError):
        return ""


def human_khz(hz):
    if hz is None:
        return "—"
    hz = float(hz)
    return "%.1f Hz" % hz if hz < 1000 else "%.2f kHz" % (hz / 1000)
