"""B10 骨架 S3：播放器式（参照 Georgia-ReBORN）

**它赌的是什么**（三行）：
  1. 用户对“顺眼”的直觉来自他天天用的播放器结构语汇
  2. 信息栏在右、视觉区在中央，是这个语汇的核心分工
  3. 判定与读数不需要抢中央，它们属于右栏

四维签名：侧栏=有·168px 导航 ｜ 卡片=有·右栏分组框 ｜ 信息=右侧独立信息栏 ｜ 曲线占比=~50%
≤200 行（本文件 1dummy 行，实测见 `check_prototypes.py`）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _kit as K  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)


def hair() -> QFrame:
    f = QFrame()
    f.setFixedHeight(1)
    f.setStyleSheet("background:%s;border:0;" % K.PAL["border"])
    return f


def lab(text, size=12, weight=400, num=False, obj="", color=None) -> QLabel:
    w = QLabel(text)
    if obj:
        w.setObjectName(obj)
    w.setFont(K.num_font(size) if num else K.tune_font(QFont(), size, weight=weight))
    if color:
        w.setStyleSheet("color:%s;" % color)
    return w


def mk_win(case):
    root = QWidget()
    root.setStyleSheet(K.qss())
    h = QHBoxLayout(root)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)

    # ---- 左：宽导航（有文字，播放器的库列表语汇）----
    nav = QWidget()
    nav.setFixedWidth(168)
    nav.setStyleSheet("background:%s;" % K.PAL["surface"])
    nv = QVBoxLayout(nav)
    nv.setContentsMargins(16, 20, 16, 16)
    nv.setSpacing(6)
    nv.addWidget(lab("音库", 15, 600))
    nv.addSpacing(8)
    for i, t in enumerate(("正在播放", "频谱图", "对比", "批次", "导出")):
        it = lab(("▸ " if i == 0 else "   ") + t, 12, 600 if i == 0 else 400,
                 color=K.PAL["primary_text"] if i == 0 else K.PAL["text_dim"])
        nv.addWidget(it)
    nv.addStretch(1)
    nv.addWidget(lab("4 个文件 · 已分析 4", 10, color=K.PAL["text_faint"]))
    h.addWidget(nav)
    h.addWidget(vline())

    # ---- 中：大视觉区（曲线）----
    mid = QWidget()
    mv = QVBoxLayout(mid)
    mv.setContentsMargins(24, 20, 24, 18)
    mv.setSpacing(12)
    mv.addWidget(K.topbar(case))
    mv.addWidget(K.verdict_band(case))
    mv.addWidget(K.PlotWidget.make(case), 1)
    h.addWidget(mid, 1)
    h.addWidget(vline())

    # ---- 右：信息栏（分组框：判定 / 读数 / 标签）----
    side = QWidget()
    side.setFixedWidth(268)
    side.setStyleSheet("background:%s;" % K.PAL["surface"])
    sv = QVBoxLayout(side)
    sv.setContentsMargins(18, 20, 18, 16)
    sv.setSpacing(14)
    sv.addWidget(grp("判定", [("结论", case["verdict"]),
                              ("成因", case["why"][:18])], case["level"]))
    sv.addWidget(grp("读数", [("真实截止", "%.2f kHz" % case["cutoff_khz"]),
                              ("占奈奎斯特", "%.0f%%" % (case["ratio"] * 100)),
                              ("集成响度", "-22.9 LUFS"),
                              ("真峰值", "+0.5 dBTP"),
                              ("动态范围", "15.4 dB"),
                              ("砖墙陡度", "67 dB/oct")]))
    sv.addWidget(grp("标签", [("格式", "FLAC"), ("采样率", "44.1 kHz")]))
    sv.addStretch(1)
    h.addWidget(side)
    return root


def vline() -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    f.setStyleSheet("background:%s;border:0;" % K.PAL["border"])
    return f


def grp(title, rows, lv=None) -> QWidget:
    """右栏的分组框——S3 的"卡片容器"就是它（与 S4 的淡底块形态不同）。"""
    box = QWidget()
    box.setStyleSheet("background:%s;border:1px solid %s;border-radius:10px;"
                      % (K.PAL["surface_raised"], K.PAL["border"]))
    v = QVBoxLayout(box)
    v.setContentsMargins(14, 12, 14, 12)
    v.setSpacing(6)
    v.addWidget(lab(title, 11, 600, color=K.PAL["text_dim"]))
    for k, val in rows:
        r = QHBoxLayout()
        r.setSpacing(8)
        r.addWidget(lab(k, 11, color=K.PAL["text_faint"]))
        r.addStretch(1)
        r.addWidget(lab(str(val), 12, 600, num=str(val)[:1].isdigit(),
                        color=(K.band(lv) if lv and k == "结论" else None)))
        v.addLayout(r)
    return box


if __name__ == "__main__":
    K.run(mk_win, "s3")
