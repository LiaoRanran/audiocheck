"""B10 骨架 S1：仪器面板（B9 方向推到底）

**它赌的是什么**（三行）：
  1. B9 的层级（判定 + 曲线 + 读数）本身是对的，之前输在执行密度
  2. 侧栏不需要文字导航，一条 56px 的图标条就够
  3. 关键数一行四个、其余收进可展开——密度由用户决定

四维签名：侧栏=有·56px 图标条 ｜ 卡片=无（1px 分隔线） ｜ 信息=顶栏 + 下方读数行 ｜ 曲线占比=~45%
≤200 行（本文件 1dummy 行，实测见 `check_prototypes.py`）。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _kit as K  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
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

    # ---- 侧栏：56px 图标条（无文字，只有 4 个方点 + 当前项高亮）----
    rail = QWidget()
    rail.setFixedWidth(56)
    rail.setStyleSheet("background:%s;" % K.PAL["surface"])
    rv = QVBoxLayout(rail)
    rv.setContentsMargins(0, 16, 0, 16)
    rv.setSpacing(14)
    for i in range(4):
        dot = QFrame()
        dot.setFixedSize(20, 20)
        dot.setStyleSheet("background:%s;border-radius:6px;"
                          % (K.PAL["primary"] if i == 0 else K.PAL["border"]))
        rv.addWidget(dot, 0, Qt.AlignHCenter)
    rv.addStretch(1)
    h.addWidget(rail)
    h.addWidget(verline())

    # ---- 主区：判定带 → 判词 → 全幅曲线 → 读数行 → 展开 ----
    main = QWidget()
    mv = QVBoxLayout(main)
    mv.setContentsMargins(28, 22, 28, 20)
    mv.setSpacing(14)
    mv.addWidget(K.topbar(case))
    mv.addWidget(K.verdict_band(case))
    row = QHBoxLayout()
    row.setSpacing(14)
    lab_v = lab("● " + case["verdict"], 26, 600, color=K.on_tint(case["level"]))
    row.addWidget(lab_v)
    row.addStretch(1)
    row.addWidget(lab("真实截止", 11, color=K.PAL["text_dim"]))
    row.addWidget(lab("%.2f kHz" % case["cutoff_khz"], 13, 600, num=True))
    mv.addLayout(row)
    mv.addWidget(lab(case["why"], 12, color=K.PAL["text_dim"]))
    plot = K.PlotWidget.make(case)
    mv.addWidget(plot, 5)                      # 曲线 ~45%
    mv.addWidget(hair())
    nums = [("砖墙陡度", "67 dB/oct"), ("集成响度", "-22.9 LUFS"),
            ("真峰值", "+0.5 dBTP"), ("动态范围", "15.4 dB")]
    nr = QHBoxLayout()
    nr.setSpacing(0)
    for i, (k, v) in enumerate(nums):
        cell = QVBoxLayout()
        cell.setSpacing(2)
        cell.addWidget(lab(k, 10, color=K.PAL["text_faint"]))
        cell.addWidget(lab(v, 15, 600, num=True))
        nr.addLayout(cell, 1)
        if i < len(nums) - 1:
            sep = QFrame()
            sep.setFixedWidth(1)
            sep.setStyleSheet("background:%s;border:0;" % K.PAL["border"])
            nr.addWidget(sep)
    mv.addLayout(nr)
    mv.addWidget(hair())
    btn = QPushButton("▸ 全部 18 项指标（另有 14 项）")
    btn.setFont(K.tune_font(QFont(), 11))
    mv.addWidget(btn, 0, Qt.AlignLeft)
    h.addWidget(main, 1)
    return root


def verline() -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    f.setStyleSheet("background:%s;border:0;" % K.PAL["border"])
    return f


if __name__ == "__main__":
    K.run(mk_win, "s1")
