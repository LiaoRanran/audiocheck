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

## 时长只有一套表

原先这里自己定义了 `FAST=170 / BASE=220`，而 `theme.Motion` 是
`fast=120 / base=200 / slow=320`——**两张表对同一个词给出不同值**，
于是"时长只许来自令牌"这句话是空的：来自哪张表？
现在 `FAST/BASE/SLOW` 直接是 `theme.Motion` 的别名，全仓一个真源。

用法：

    from . import motion
    motion.fade(widget, self)                 # 一次性淡入
    motion.fade(widget, self, end=0.0, cleanup_extra=...)   # 淡出后拆掉
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

from .theme import Motion

#: 缓动曲线的名字表。用**名字**而不是直接传枚举：调用方不必 import Qt。
CURVES = {
    "out": QEasingCurve.OutCubic,       # 进入：快起慢停（最常用的"轻快"）
    "in": QEasingCurve.InCubic,         # 离开：
    "in_out": QEasingCurve.InOutCubic,  # 往复：
    "bounce": QEasingCurve.OutBack,     # 提示：略微过冲，抓注意力
}

#: 时长（毫秒）。**这三个名字是 `theme.Motion` 的别名，不是另一张表**——
#: 早先这里写 170/220，与 theme.Motion 的 120/200 不一致，"时长只许来自
#: 令牌"于是落了空。保留 FAST/BASE 名字是因为调用点读起来更短，
#: 且动效要能在没有 Theme 对象的场景（探针、脚本）里工作。
FAST, BASE, SLOW = Motion.fast, Motion.base, Motion.slow


def _run(host, group, widget, eff, cleanup_extra=None):
    """启动动画并挂上**唯一**的清理回调。"""

    def _cleanup(_=None):
        # 用 isValid 守住：部件可能在动画结束前就被下一轮替换掉（那时 effect
        # 随部件一起没了），而 `setGraphicsEffect(None)` 本身也会让 Qt 释放旧效果。
        # 不判断就会打在已删除的 C++ 对象上（实测 RuntimeError）。
        from shiboken6 import isValid

        if widget is not None and isValid(widget):
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


def fade(widget, host, *, ms: int = BASE, start: float = 0.0, end: float = 1.0,
         delay: int = 0, curve: str = "out", cleanup_extra=None):
    """淡入 / 淡出。结束时自动拆除效果与动画。

    `end=0.0` 即淡出；`cleanup_extra` 在清理时回调（用于"淡出完了再拆部件"——
    Toast 的退场就走这条，见 `widgets.ToastHost._dismiss`）。
    """
    if widget is None:
        return
    eff = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(eff)
    anim = QPropertyAnimation(eff, b"opacity", host)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(CURVES[curve])
    _run(host, _group(host, anim, delay), widget, eff, cleanup_extra)


def stagger(widgets, host, *, step: int = 14, ms: int = FAST, start: float = 0.0):
    """一屏控件依次浮现：让"内容到达"这件事有一次视觉上的交代。"""
    for i, w in enumerate(widgets):
        fade(w, host, ms=ms, start=start, delay=i * step)


def tween(target, prop: bytes, host, *, start: float, end: float, ms: int = BASE,
          curve: str = "out"):
    """对**任意属性**做一次补间。不透明度以外的属性走这里。

    为什么需要它：`fade` / `pulse` 都要给部件套 `QGraphicsOpacityEffect`
    （多一层合成），而有些属性本来就在部件自身上——仪表环的进度值就是。
    给它套效果不只是浪费：效果会连同子部件一起被光栅化，观感发虚。

    仍然走 `_run` 的清理纪律：补间对象用完即拆，不长期挂在部件上。
    调用方若要连续触发（读数连续变化），自己持有返回值并在下一次前 `stop()`
    （`Gauge.set_data` 是这么做的）——否则两个补间同时写同一个属性会打架。
    """
    anim = QPropertyAnimation(target, prop, host)
    anim.setStartValue(start)
    anim.setEndValue(end)
    anim.setDuration(ms)
    anim.setEasingCurve(CURVES[curve])
    return _run(host, anim, None, None)


def pulse(widget, host, *, ms: int = SLOW, loops: int = 2, low: float = 0.45,
          curve: str = "in_out"):
    """脉冲提醒：**由信息驱动**（例如结论是"有疑/不达标"时才脉一次）。

    为什么强调"信息驱动"：动效一旦变成常驻装饰，就会持续吃 CPU，
    也失去提示作用——用户会习惯性忽略它。只在"值得看一眼"的时刻动。

    `ms` 默认取 `Motion.slow`（320）——它原先写死 520，而 520 不在任何一张
    时长表里（正是"手写毫秒字面量"要清的那类散点）。
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
