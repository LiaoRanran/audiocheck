"""B10 骨架 S4：判定流（叙事式）

**它赌的是什么**（三行）：
  1. 用户要的是“结论先讲清楚”，曲线是证据附件
  2. C15 的四层（现象 → 成因 → 画像 → 用途边界）可以当版面骨架
  3. 左侧 40% 讲故事、右侧 60% 给证据，比反过来更接近人读结论的顺序

四维签名：侧栏=无 ｜ 卡片=有·四层淡底块 ｜ 信息=左侧叙事栏 40% ｜ 曲线占比=~60%
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


NARRATIVE = (
    ("现象", "%(v)s", "内容到 %(khz).2f kHz，占奈奎斯特 %(ratio).0f%%。"),
    ("成因", "编码器切痕", "截止处 %.0f dB/oct 的陡降不是自然衰减，是转码留下的。"
                          % 67),
    ("画像", "192–256 kbps 有损", "16 kHz 附近一刀切，与主流有损档位吻合。"),
    ("用途边界", "可听但不适合当基准", "做听感练习可用；做器材评测的参考源不合适。"),
)


def mk_win(case):
    root = QWidget()
    root.setStyleSheet(K.qss())
    h = QHBoxLayout(root)
    h.setContentsMargins(0, 0, 0, 0)
    h.setSpacing(0)

    # ---- 左 40%：判定流（四层叙事，每层一块淡底）----
    left = QWidget()
    lv = QVBoxLayout(left)
    lv.setContentsMargins(28, 22, 24, 20)
    lv.setSpacing(12)
    lv.addWidget(K.topbar(case))
    lv.addWidget(K.verdict_band(case))
    lv.addWidget(lab("● " + case["verdict"], 26, 600))
    lv.addWidget(lab(case["why"], 12, color=K.PAL["text_dim"]))
    lv.addSpacing(4)
    for i, (k, title, body) in enumerate(NARRATIVE):
        lv.addWidget(block(k, title % {"v": case["verdict"], "khz": case["cutoff_khz"],
                                       "ratio": case["ratio"] * 100} if "%" in title else title,
                           body, case["level"] if i == 0 else None))
    lv.addStretch(1)
    h.addWidget(left, 2)
    h.addWidget(vline())

    # ---- 右 60%：曲线证据 ----
    right = QWidget()
    rv = QVBoxLayout(right)
    rv.setContentsMargins(24, 22, 28, 20)
    rv.setSpacing(12)
    rv.addWidget(lab("证据 · 频谱包络", 11, 600, color=K.PAL["text_dim"]))
    rv.addWidget(K.PlotWidget.make(case), 1)
    rv.addWidget(hair())
    foot = QHBoxLayout()
    foot.setSpacing(18)
    for k, v in (("真实截止", "%.2f kHz" % case["cutoff_khz"]),
                 ("占奈奎斯特", "%.0f%%" % (case["ratio"] * 100)),
                 ("集成响度", "-22.9 LUFS"), ("动态范围", "15.4 dB")):
        foot.addWidget(lab(k, 10, color=K.PAL["text_faint"]))
        foot.addWidget(lab(v, 13, 600, num=True))
    foot.addStretch(1)
    rv.addLayout(foot)
    h.addWidget(right, 3)
    return root


def vline() -> QFrame:
    f = QFrame()
    f.setFixedWidth(1)
    f.setStyleSheet("background:%s;border:0;" % K.PAL["border"])
    return f


def block(tag: str, title: str, body: str, lv) -> QWidget:
    """四层叙事的一块：**淡底**（tint）而不是描边卡片——与 S3 的分组框区分。"""
    box = QWidget()
    bg = K.tint(lv) if lv else K.PAL["surface"]
    box.setStyleSheet("background:%s;border:0;border-radius:10px;" % bg)
    v = QVBoxLayout(box)
    v.setContentsMargins(14, 10, 14, 10)
    v.setSpacing(3)
    v.addWidget(lab(tag, 10, 600, color=K.PAL["text_dim"]))
    v.addWidget(lab(title, 14, 600))
    v.addWidget(lab(body, 11, color=K.PAL["text_dim"]))
    return box


if __name__ == "__main__":
    K.run(mk_win, "s4")
