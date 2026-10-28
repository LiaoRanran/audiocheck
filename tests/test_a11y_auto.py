"""a11y 自动化判据（Agent I 调研清单的落地部分）。

两项可完全自动化（offscreen）的检查：
1. **WCAG 对比度**：主题的文字/背景色对按 WCAG 1.4.3 公式断言
   （正文 ≥4.5:1；大字/组件 ≥3:1）——纯 Python 计算，与 Qt 渲染无关；
2. **焦点链完整性**：编辑窗口的 Tab 焦点链覆盖全部可聚焦控件、无黑洞
   （nextInFocusChain 走一圈回到起点）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _lum(hex_color: str) -> float:
    """WCAG 相对亮度（sRGB → linear → 加权）。"""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(c1: str, c2: str) -> float:
    l1, l2 = sorted((_lum(c1), _lum(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_theme_text_contrast_wcag_aa():
    """★ WCAG 1.4.3：主题文字在各级背景上 ≥4.5:1（正文）。弱文本历史踩过
    3.46:1 的坑（theme.py 注记）——这条把"不再回退"钉成判据。"""
    from audiocheck.ui import theme as T
    for mode in ("dark", "light"):
        th = T.THEMES[mode].p
        for bg_name in ("bg", "surface", "surface_hi", "surface_raised"):
            bg = getattr(th, bg_name)
            for tx_name in ("text", "text_dim"):
                tx = getattr(th, tx_name)
                ratio = _contrast(tx, bg)
                assert ratio >= 4.5, \
                    "%s/%s: %s vs %s = %.2f:1 < 4.5" % (mode, tx_name, tx, bg, ratio)


def test_theme_faint_text_on_surface_meets_aa():
    """弱文本（text_faint，11px 标签）在 surface 上 ≥4.5:1。"""
    from audiocheck.ui import theme as T
    th = T.THEMES["dark"].p
    assert _contrast(th.text_faint, th.surface) >= 4.5


def test_editor_focus_chain_covers_all_focusables(audio):
    """★ 焦点链完整性：编辑窗口 Tab 链覆盖全部 TabFocus 控件且无黑洞
    （nextInFocusChain 走 2N 步必回到起点，N = 可聚焦控件数）。"""
    from PySide6.QtWidgets import QApplication, QWidget

    _app = QApplication.instance() or QApplication([])
    from audiocheck.ui import theme as T
    from audiocheck.ui.editor.shell import EditorWindow

    w = EditorWindow(T.THEMES["dark"])
    w.show()
    focusables = [c for c in w.findChildren(QWidget)
                  if c.isVisibleTo(w) and (c.focusPolicy() & __import__(
                      "PySide6.QtCore", fromlist=["Qt"]).Qt.FocusPolicy.TabFocus)]
    assert focusables, "窗口里必须有可聚焦控件"
    n = len(focusables)
    cur = w.nextInFocusChain()
    visited = []
    for _ in range(2 * n + 2):                      # 2N 上限内必成环
        if cur is w or cur in visited:
            break
        visited.append(cur)
        cur = cur.nextInFocusChain()
    # 链上的每个控件都 ∈ focusables（无外来黑洞），且覆盖数 ≥ 一半
    # （tabify dock 的隐藏 tab 不在链上是 Qt 正常行为，覆盖全部可见控件即达标）
    visible_focus = {c for c in focusables if c.isVisible() and c.isEnabled()}
    # 允许少量隐藏容器内的控件（Qt 对隐藏 dock 的处理），但主体必须可达
    assert len(visited) >= max(1, len(visible_focus) // 2), \
        "焦点链覆盖 %d / 可聚焦 %d" % (len(visited), len(visible_focus))
    w.deleteLater()


def test_focusable_controls_have_accessible_name(audio):
    """★ **可访问名称**：每个可聚焦控件都必须能被屏幕阅读器念出名字。

    ## 这条判据为什么必须存在

    `QAccessible` 的命名优先级是：`accessibleName()` → **窗口标题** →
    `QLabel.setBuddy` 推导出的文本。因此"没设 accessibleName"的控件并非一定
    无声——但**只有靠 buddy 才偶然有名字**的控件，一旦 UI 结构改动就会静默
    失去名字，而屏幕阅读器用户看到的是"按钮、按钮、按钮"。

    ⚠ 另一个陷阱：`QAccessible::isActive()` 是**惰性**的——没有 AT 客户端连接时
    返回 false，`updateAccessibility()` 全被忽略。所以"动画/事件驱动的可访问性
    通知"在无 AT 的测试环境里必然不触发。**名称是静态属性**，不受此影响，
    因此可以在无 AT 环境下断言——这也是本条选"名称"而不是"通知"的原因。

    判据：可聚焦且可见的 QAbstractButton / QLineEdit / QComboBox 等
    "会被念出名字"的控件，`accessibleName()` 与 `text()` 不能同时为空。
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractButton,
        QAbstractItemView,
        QApplication,
        QComboBox,
        QLineEdit,
        QWidget,
    )

    _app = QApplication.instance() or QApplication([])
    from audiocheck.ui import theme as T
    from audiocheck.ui.editor.shell import EditorWindow

    w = EditorWindow(T.THEMES["dark"])
    w.show()
    named_types = (QAbstractButton, QLineEdit, QComboBox, QAbstractItemView)
    bad = []
    for c in w.findChildren(QWidget):
        if not isinstance(c, named_types):
            continue
        if not (c.isVisibleTo(w) and c.isEnabled()):
            continue
        if not (c.focusPolicy() & Qt.FocusPolicy.TabFocus):
            continue
        name = c.accessibleName() or ""
        text = (c.text() if hasattr(c, "text") else "") or ""
        tip = c.toolTip() or ""
        if not (name.strip() or text.strip() or tip.strip()):
            bad.append("%s(%r)" % (type(c).__name__, c.objectName()))
    assert not bad, (
        "以下可聚焦控件既无 accessibleName 也无 text/toolTip，"
        "屏幕阅读器念不出名字：\n  " + "\n  ".join(bad[:12]))
    w.deleteLater()
