"""动效：令牌化、可复用、**永不碰 matplotlib**。

## 为什么要单独一个模块

1. 动效参数原来散在 `window.py` 的私有方法里：想调"整体手感"要全局搜，
   而 `theme.Motion` 那三个令牌（fast/base/slow）就只是声明、没有落点。
2. 动效对象必须**用完即拆**——这项目刚修过"色条永不回收"那类泄漏，
   `QGraphicsOpacityEffect` 会让部件多一层合成，留着就是一直付代价。
   集中在一处才守得住这条纪律（含 `isValid` 判断，见下）。
3. **它的边界必须写在门口**：画布一次重画是百毫秒量级（`perf_probe.py` 有数），
   拿它做动画会直接顶穿帧预算。所以这里的 API 只作用于**轻量控件**。

## 为什么没有 `slide`（位置动画）

布局管理的控件没有可靠的位置动画：`QPropertyAnimation` 改完 `pos`，
下一轮 layout 会把它拉回原位，观感变成"抖一下"。硬做出来的是假动效，
所以这里只提供**不破坏布局**的属性（不透明度、以及基于它的组合）。

用法：

    from . import motion
    motion.fade(widget, self)                 # 一次性淡入
    motion.stagger(cards, self)               # 依次浮现
    motion.pulse(badge, self, loops=2)        # 脉冲提醒（信息驱动，不是装饰）
"""
from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve,
    QPropertyAnimation,
    QSequentialAnimationGroup,
)
from PySide6.QtWidgets import QGraphicsOpacityEffect

#: 缓动曲线的名字表。用**名字**而不是直接传枚举：调用方不必 import Qt。
CURVES = {
    "out": QEasingCurve.OutCubic,       # 进入：快起慢停（最常用的"轻快"）
    "in": QEasingCurve.InCubic,         # 离开：
    "in_out": QEasingCurve.InOutCubic,  # 往复：
    "bounce": QEasingCurve.OutBack,     # 提示：略微过冲，抓注意力
}

#: 默认时长（毫秒）。与 `theme.Motion` 的 fast/base/slow 同量级，
#: 但动效自己也要能在没有 theme 的场景（探针、脚本）里工作。
FAST, BASE = 170, 220


def _run(host, group, widget, eff, cleanup_extra=None):
    """启动动画并挂上**唯一**的清理回调。"""

    def _cleanup(_=None):
        # 用 isValid 守住：部件可能在动画结束前就被下一轮替换掉（那时 effect
        # 随部件一起没了），而 `setGraphicsEffect(None)` 本身也会让 Qt 释放旧效果。
        # 不判断就会打在已删除的 C++ 对象上（实测 RuntimeError）。
        from shiboken6 import isValid

        if isValid(widget):
            widget.setGraphicsEffect(None)
        if isValid(group):
            group.deleteLater()
        if cleanup_extra is not None:
            cleanup_extra()

    group.finished.connect(_cleanup)
    group.start()
    return group


def _group(host, anim, delay: int):
    if not delay:
        return anim
    seq = QSequentialAnimationGroup(host)
    seq.addPause(delay)
    seq.addAnimation(anim)
    return seq


def fade(widget, host, *, ms: int = FAST, start: float = 0.0, delay: int = 0,
         curve: str = "out"):
    """淡入。`delay` 用于错峰；结束时自动拆除效果与动画。"""
    if widget is None:
        return
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", host)
    anim.setStartValue(start)
    anim.setEndValue(1.0)
    anim.setDuration(ms)
    anim.setEasingCurve(CURVES[curve])
    _run(host, _group(host, anim, delay), widget, eff)


def stagger(widgets, host, *, step: int = 14, ms: int = FAST, start: float = 0.0):
    """一屏控件依次浮现：让"内容到达"这件事有一次视觉上的交代。"""
    for i, w in enumerate(widgets):
        fade(w, host, ms=ms, start=start, delay=i * step)


def pulse(widget, host, *, ms: int = 520, loops: int = 2, low: float = 0.45,
          curve: str = "in_out"):
    """脉冲提醒：**由信息驱动**（例如结论是"有疑/不达标"时才脉一次）。

    为什么强调"信息驱动"：动效一旦变成常驻装饰，就会持续吃 CPU，
    也失去提示作用——用户会习惯性忽略它。只在"值得看一眼"的时刻动。
    """
    if widget is None:
        return
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    seq = QSequentialAnimationGroup(host)
    for _ in range(loops):
        down = QPropertyAnimation(eff, b"opacity", host)
        down.setStartValue(1.0)
        down.setEndValue(low)
        down.setDuration(ms // 2)
        down.setEasingCurve(CURVES[curve])
        up = QPropertyAnimation(eff, b"opacity", host)
        up.setStartValue(low)
        up.setEndValue(1.0)
        up.setDuration(ms // 2)
        up.setEasingCurve(CURVES[curve])
        seq.addAnimation(down)
        seq.addAnimation(up)
    _run(host, seq, widget, eff)
