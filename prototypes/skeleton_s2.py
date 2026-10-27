"""B10 骨架 S2：曲线即版面（最激进）

**它赌的是什么**（三行）：
  1. 这个工具的本质就是“看曲线”，一切版面元素都是干扰
  2. 判词与关键数应当是**画在曲线上的注记**，不是独立区域
  3. 占比用一条标记线表达就够，不需要仪表

四维签名：侧栏=无 ｜ 卡片=无（注记即版面） ｜ 信息=图内注记 ｜ 曲线占比=~92%
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
    v = QVBoxLayout(root)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(0)

    # ---- 一条顶栏：文件信息 + 判定 + hero（恒锚，其余全交给曲线）----
    bar = QWidget()
    bar.setFixedHeight(64)
    bar.setStyleSheet("background:%s;" % K.PAL["surface"])
    bh = QHBoxLayout(bar)
    bh.setContentsMargins(28, 0, 28, 0)
    bh.setSpacing(18)
    bh.addWidget(lab(case["file"], 13, 600))
    bh.addWidget(lab("● " + case["verdict"], 12, 600, color=K.band(case["level"])))
    bh.addStretch(1)
    # ⚠ hero 必须打 `objectName="Hero"`：S2 是**手搓顶栏**（不走 `K.topbar`），
    # 漏了这个名字，判据 5「恒定锚三件套在场」在它身上就检不到 hero——
    # 而 hero **是真在场的**（40px 的 "16.01"）。判据检的是结构，不是肉眼。
    bh.addWidget(lab("%.2f" % case["cutoff_khz"], 40, 600, num=True, obj="Hero"))
    bh.addWidget(lab("kHz 真实截止", 11, color=K.PAL["text_dim"]))
    v.addWidget(bar)
    v.addWidget(hair())

    # ---- 曲线吃掉其余全部：判词/成因/关键数/占比都是**图内注记** ----
    notes = [
        ((0.035, 0.13), "● " + case["verdict"], 26),
        ((0.035, 0.21), case["why"], 12),
        ((0.035, 0.90), "砖墙陡度 67 dB/oct", 12),
        ((0.30, 0.90), "集成响度 -22.9 LUFS", 12),
        ((0.56, 0.90), "真峰值 +0.5 dBTP", 12),
        ((0.80, 0.90), "动态范围 15.4 dB", 12),
        ((0.72, 0.30), "占奈奎斯特 %.0f%%" % (case["ratio"] * 100), 12),
    ]
    plot = K.PlotWidget.make(case, notes=notes, pad=26)
    v.addWidget(plot, 1)
    return root


if __name__ == "__main__":
    K.run(mk_win, "s2")
